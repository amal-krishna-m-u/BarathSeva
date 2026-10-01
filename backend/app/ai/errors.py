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
#: and not always paired with a 429 status; some Google API surfaces also
#: report quota exhaustion as a 403 with reason ``rateLimitExceeded`` /
#: ``quotaExceeded``.
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

    For ``httpx.HTTPStatusError``, the body sniff runs on every status (it is
    redundant but harmless on 429) and *can* override an ``AUTH``
    classification — Google documents 403 with reason ``rateLimitExceeded``
    / ``quotaExceeded`` on some API surfaces, which is exactly this shape.
    Misreading a real quota-403 as AUTH would route the fallback ladder to
    the stub forever and never try the rate-limit fallback for that failure
    mode; misreading a genuine bad-key 401/403 as RATE_LIMITED instead costs
    one extra (harmless) hop to a fallback provider with its own key, and the
    original error string is still preserved for the operator. AUTH is the
    recoverable misclassification, so the sniff is allowed to win there.

    The sniff does **not** override ``SERVER``: a 5xx carrying incidental
    quota-shaped text is far more likely a genuine server fault — Google has
    no documented 5xx quota analogue — and overriding it would widen the
    rate-limit fallback beyond what the spec asks for ("Gemini only when
    rate limited").
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        kind = classify_http_status(status)
        if kind not in (ErrorKind.RATE_LIMITED, ErrorKind.SERVER):
            # kind is AUTH or OTHER here — both are allowed to be upgraded.
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
