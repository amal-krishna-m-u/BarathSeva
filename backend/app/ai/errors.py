"""Provider failure classification.

An ``InferenceResult.error`` has always been a free-form human-readable
string — useful for a log line, useless for a policy decision. The fallback
policy this platform needs (NIM primary, Gemini fallback *only* on rate
limiting, stub for everything else) cannot be expressed against a string: you
cannot safely ``"429" in error_message`` your way to correctness. This module
gives every provider failure a small, closed vocabulary instead, so the
caller can branch on *why* a call failed rather than parse prose describing
it.

Classification here is deliberately shallow and provider-agnostic. It reads
only ``httpx`` exception types and HTTP status codes/bodies — never
``openai.*`` or ``google.*`` exception classes, because those SDKs are
optional dependencies and may not be installed. A provider that wraps a
vendor SDK is responsible for translating the SDK's own exceptions into one
of the forms this module understands (or into an ``httpx`` exception
directly, which is what the NIM and Gemini REST adapters do).
"""

from __future__ import annotations

import json
import re

import httpx

#: Case-insensitive signal that a response body carries a quota/rate-limit
#: error even though the HTTP status code alone does not say so. Gemini's
#: REST API in particular returns ``RESOURCE_EXHAUSTED`` in the JSON body,
#: and not always paired with a 429 status.
_RATE_LIMIT_BODY_RE = re.compile(
    r"resource_exhausted|quota|rate limit|too many requests", re.IGNORECASE
)


class ErrorKind:
    """Closed vocabulary for why a provider call failed.

    String-valued (not an ``enum.Enum``) so it serialises trivially into
    ``InferenceResult`` dataclasses, logs, and JSON responses without a
    custom encoder.
    """

    RATE_LIMITED = "RATE_LIMITED"
    AUTH = "AUTH"
    TIMEOUT = "TIMEOUT"
    BAD_RESPONSE = "BAD_RESPONSE"
    NETWORK = "NETWORK"
    SERVER = "SERVER"
    OTHER = "OTHER"


def classify_http_status(status: int) -> str:
    """Map a bare HTTP status code to an :class:`ErrorKind`."""
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if status in (401, 403):
        return ErrorKind.AUTH
    if 500 <= status < 600:
        return ErrorKind.SERVER
    return ErrorKind.OTHER


def is_rate_limit_body(body: str) -> bool:
    """True if a response body text reads as a quota/rate-limit error.

    Standalone so a provider can call it directly on raw response text —
    including a ``200``/non-error status that still embeds a quota error in
    its JSON payload — without first having to construct or catch an
    exception.
    """
    if not body:
        return False
    return bool(_RATE_LIMIT_BODY_RE.search(body))


def classify_exception(exc: Exception) -> str:
    """Map a caught exception to an :class:`ErrorKind`.

    Branch order matters. ``httpx.TimeoutException`` and
    ``httpx.ConnectError`` are both subclasses of ``httpx.TransportError``,
    so the timeout check must come first — a naive "is this a
    ``TransportError``" check first would swallow timeouts and misreport
    them as ``NETWORK``.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        kind = classify_http_status(status)
        if kind != ErrorKind.RATE_LIMITED:
            # A non-429 status can still be a disguised rate limit (Gemini's
            # RESOURCE_EXHAUSTED is the motivating case) — sniff the body
            # before accepting the status-code-only classification.
            try:
                body = exc.response.text
            except Exception:
                body = ""
            if is_rate_limit_body(body):
                return ErrorKind.RATE_LIMITED
        return kind

    if isinstance(exc, httpx.TimeoutException):
        return ErrorKind.TIMEOUT

    if isinstance(exc, httpx.TransportError):
        return ErrorKind.NETWORK

    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return ErrorKind.BAD_RESPONSE

    return ErrorKind.OTHER
