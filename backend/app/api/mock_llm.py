"""Mock LLM endpoint — stands in for NVIDIA NIM (Kimi K3) and Gemini.

Nothing here is a real model. It exists for one reason: the machine running
this prototype has no API key for any provider, yet the 429 → Gemini-fallback
→ stub ladder (Task 2) has to be provable without burning a real free-tier
quota. This module speaks the two wire shapes the real providers speak —
OpenAI-compatible chat completions (what the NVIDIA/NIM client calls) and the
Gemini REST `generateContent` shape (what the Gemini fallback client calls) —
so the provider code under test runs completely unmodified against
``nvidia_base_url=http://.../mock/llm/v1`` or the Gemini-shaped path, with no
behavioural branch for "we are in a test".

Decisions are not invented here. The request text is parsed back into the
same ``context`` dict ``app.ai.prompts`` built it from, and handed to the real
``StubProvider`` — the same deterministic rule engine used as the production
fallback-of-last-resort — so the mock's verdicts are, by construction, exactly
what the stub would decide for the same input. There is no separate category
list or priority ladder to drift out of sync with ``app/ai/taxonomy.py``.

Failure injection is driven entirely by request headers (``X-Mock-Failure``,
``X-Mock-Failure-Count``, ``X-Mock-Delay-Seconds``) rather than shared
mutable test state, so a test only has to set a header — see
``_maybe_fail`` below for the exact contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.ai.base import InferenceRequest, Task
from app.ai.stub import StubProvider
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock/llm", tags=["mock-llm"])

_stub = StubProvider()

#: Recognised X-Mock-Failure values and what each one means. Kept close to
#: _maybe_fail so the contract and the implementation cannot drift apart.
_FAILURE_MODES = frozenset({"rate_limit", "auth", "server", "timeout", "garbage"})

#: Short default so a test suite never actually waits ~20s (the real
#: ai_request_timeout_seconds). Override per-request with X-Mock-Delay-Seconds.
_DEFAULT_TIMEOUT_DELAY_SECONDS = 0.5

#: Per-failure-mode call counters for X-Mock-Failure-Count. Keyed by mode
#: name (not globally shared) so a test asserting "rate_limit fails twice"
#: cannot be perturbed by another test that happens to also inject "auth"
#: failures around the same time. Reset with POST /mock/llm/_reset.
_failure_counts: dict[str, int] = {}


def reset_failure_counts() -> None:
    """Clear all X-Mock-Failure-Count counters. Importable by tests directly;
    also reachable over HTTP via the _reset route below for black-box use."""
    _failure_counts.clear()


@router.post("/_reset", include_in_schema=False)
def _reset_endpoint() -> dict[str, str]:
    reset_failure_counts()
    return {"status": "reset"}


def _should_fail_this_call(mode: str, count_header: Optional[str]) -> bool:
    """Without X-Mock-Failure-Count, every call fails. With it, only the
    first N calls for that mode fail and the rest succeed — so a test can
    exercise "fails twice then recovers" without any teardown."""
    if count_header is None:
        return True
    try:
        limit = int(count_header)
    except ValueError:
        return True
    seen = _failure_counts.get(mode, 0)
    if seen >= limit:
        return False
    _failure_counts[mode] = seen + 1
    return True


def _rate_limit_body(gemini: bool) -> dict[str, Any]:
    if gemini:
        # Real Gemini signals quota exhaustion with this exact status string;
        # the fallback classifier (another task) sniffs for it.
        return {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted (e.g. check quota).",
                "status": "RESOURCE_EXHAUSTED",
            }
        }
    return {
        "error": {
            "message": "Rate limit reached for requests. Please try again later.",
            "type": "rate_limit_error",
            "code": "rate_limit_exceeded",
        }
    }


async def _maybe_fail(request: Request, *, gemini: bool) -> Optional[Response]:
    """Inspect X-Mock-Failure and friends; return a Response to short-circuit
    the request, or None to let the caller proceed normally.

    Contract:
      X-Mock-Failure: rate_limit | auth | server | timeout | garbage
        rate_limit -> HTTP 429, quota-shaped body (RESOURCE_EXHAUSTED on the
                      Gemini route specifically).
        auth       -> HTTP 401.
        server     -> HTTP 500.
        timeout    -> sleeps X-Mock-Delay-Seconds (default 0.5s) past the
                      caller's own client timeout, then falls through to a
                      normal 200. A test sets a client timeout shorter than
                      the delay so it never actually waits for the sleep.
        garbage    -> HTTP 200 whose body is not valid JSON.
      X-Mock-Failure-Count: N
        Fail only the first N calls for this mode, then succeed. Omit to
        fail every call. Counters are per-mode; reset via
        POST /mock/llm/_reset or reset_failure_counts().
    """
    mode = request.headers.get("x-mock-failure")
    if not mode:
        return None
    mode = mode.strip().lower()
    if mode not in _FAILURE_MODES:
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown X-Mock-Failure mode: {mode!r}"},
        )

    count_header = request.headers.get("x-mock-failure-count")
    if not _should_fail_this_call(mode, count_header):
        return None

    logger.info("mock LLM injecting failure mode=%s gemini=%s", mode, gemini)

    if mode == "rate_limit":
        return JSONResponse(status_code=429, content=_rate_limit_body(gemini))
    if mode == "auth":
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key.", "type": "authentication_error"}},
        )
    if mode == "server":
        return JSONResponse(
            status_code=500,
            content={"error": {"message": "Internal server error (simulated).", "type": "server_error"}},
        )
    if mode == "timeout":
        delay_header = request.headers.get("x-mock-delay-seconds")
        try:
            delay = float(delay_header) if delay_header else _DEFAULT_TIMEOUT_DELAY_SECONDS
        except ValueError:
            delay = _DEFAULT_TIMEOUT_DELAY_SECONDS
        await asyncio.sleep(delay)
        return None  # fall through to a normal response for any client patient enough to wait
    if mode == "garbage":
        return Response(
            status_code=200,
            media_type="application/json",
            content="{not valid json, this is deliberately malformed }}",
        )
    return None  # pragma: no cover - _FAILURE_MODES covers every branch above


# --------------------------------------------------------------------------
# Reconstructing the inference context from prompt text.
#
# app.ai.prompts builds each request as (system, user, context). The wire
# formats below only carry system/user text, so the context has to be parsed
# back out of it. Three of the five tasks (cluster_summary, social,
# resolution) embed their whole context dict as a JSON blob in the user text
# already — _json_after() recovers it exactly via json.JSONDecoder.raw_decode,
# which tolerates whatever trails the blob instead of requiring an exact
# string match. The other two (verify, classify) compose their context into
# plain sentences, so those are recovered with narrow regexes instead.
# --------------------------------------------------------------------------

_TASK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (Task.VERIFY, ("verification gate", "is this a genuine civic infrastructure issue")),
    (Task.CLASSIFY, ("you classify civic complaints", "classify the category and priority")),
    (Task.CLUSTER_SUMMARY, ("summarise the geographic context", "summarise this geographic context")),
    (Task.SOCIAL, ("public accountability posts", "write the public accountability post")),
    (
        Task.RESOLUTION,
        (
            "closing message a citizen receives",
            "write the citizen's resolution message",
            "write the citizen’s resolution message",
        ),
    ),
)


def _detect_task(text: str) -> Optional[str]:
    lowered = text.lower()
    for task, markers in _TASK_MARKERS:
        if any(marker in lowered for marker in markers):
            return task
    return None


def _collect_text(obj: Any, acc: list[str]) -> None:
    """Walk an arbitrary JSON body and collect every string found under a
    "text" or "content" key, in document order. Works for both wire shapes
    (OpenAI's messages[].content, Gemini's parts[].text) without needing to
    know which one we were sent, and silently ignores image parts (they
    carry no "text"/"content" key) rather than choking on them."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("text", "content") and isinstance(value, str) and value:
                acc.append(value)
            else:
                _collect_text(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_text(item, acc)


def _combined_text(payload: Any) -> str:
    acc: list[str] = []
    _collect_text(payload, acc)
    return "\n\n".join(acc)


def _json_after(text: str, marker: str) -> Optional[Any]:
    """Find `marker` in text, then parse the first JSON value that starts
    after it — ignoring whatever text follows the value. Using
    json.JSONDecoder.raw_decode (rather than a regex) means this does not
    need to guess where the blob ends."""
    idx = text.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    brace_idx = None
    for i in range(start, len(text)):
        if text[i] in "{[":
            brace_idx = i
            break
    if brace_idx is None:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, brace_idx)
    except json.JSONDecodeError:
        return None
    return obj


_QUOTED_REPORT_RE = re.compile(r'Citizen report:\s*"(.*?)"', re.DOTALL)
_PHOTO_RE = re.compile(r"Photograph attached:\s*(yes|no)(\s*\(unreadable\))?", re.IGNORECASE)
_SCORE_RE = re.compile(r"authenticity score:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_NEARBY_RE = re.compile(r"Corroborating reports nearby:\s*(\d+)", re.IGNORECASE)
_WARD_RE = re.compile(r"^Ward:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _verify_context(text: str) -> dict[str, Any]:
    description = ""
    match = _QUOTED_REPORT_RE.search(text)
    if match:
        description = match.group(1)

    has_photo = False
    image_readable = True
    match = _PHOTO_RE.search(text)
    if match:
        has_photo = match.group(1).lower() == "yes"
        image_readable = not bool(match.group(2))

    score = 0.0
    match = _SCORE_RE.search(text)
    if match:
        try:
            score = float(match.group(1))
        except ValueError:
            score = 0.0

    signals = _json_after(text, "Deterministic evidence findings:") or []
    signal_codes = [s.get("code") for s in signals if isinstance(s, dict)]

    return {
        "description": description,
        "has_photo": has_photo,
        "image_readable": image_readable,
        "authenticity_score": score,
        "signal_codes": signal_codes,
    }


def _classify_context(text: str) -> dict[str, Any]:
    description = ""
    match = _QUOTED_REPORT_RE.search(text)
    if match:
        description = match.group(1)

    nearby = 0
    match = _NEARBY_RE.search(text)
    if match:
        nearby = int(match.group(1))

    ward = None
    match = _WARD_RE.search(text)
    if match:
        value = match.group(1).strip()
        ward = None if value.lower() == "unknown" else value

    return {"description": description, "nearby_count": nearby, "ward_name": ward}


def _build_context(task: str, text: str) -> dict[str, Any]:
    if task == Task.VERIFY:
        return _verify_context(text)
    if task == Task.CLASSIFY:
        return _classify_context(text)
    if task == Task.CLUSTER_SUMMARY:
        return _json_after(text, "Complaint context:") or {}
    if task == Task.SOCIAL:
        return _json_after(text, "Complaint facts:") or {}
    if task == Task.RESOLUTION:
        return _json_after(text, "Resolved complaint facts:") or {}
    return {}


def _infer_content(task: str, text: str) -> dict[str, Any]:
    """Run the real StubProvider over the reconstructed context, then shape
    the result to match the schema app.ai.prompts advertises for this task —
    so the JSON in the response is exactly what a provider parser expects,
    with real bool/number types (never the strings "true"/"false")."""
    context = _build_context(task, text)
    result = _stub.infer(InferenceRequest(task=task, system="", user="", schema={}, context=context))
    data = dict(result.data)
    if task == Task.VERIFY:
        data.pop("matched_phrases", None)
        data["rationale"] = result.rationale
        data["confidence"] = result.confidence
    elif task == Task.CLASSIFY:
        data["rationale"] = result.rationale
        data["confidence"] = result.confidence
    else:
        data["confidence"] = result.confidence
    return data


def _resolve_task_and_content(payload: Any) -> dict[str, Any]:
    text = _combined_text(payload)
    task = _detect_task(text) or Task.VERIFY
    return _infer_content(task, text)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """OpenAI-compatible chat completions. Point nvidia_base_url at
    http://.../mock/llm/v1 and the real NVIDIA/NIM provider code runs
    unmodified against this. The decoded payload is a JSON object, as a
    string, in choices[0].message.content — never a native object, because
    that is how every OpenAI-shaped API actually returns it."""
    failure = await _maybe_fail(request, gemini=False)
    if failure is not None:
        return failure

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    content = _resolve_task_and_content(payload)
    model = (payload.get("model") if isinstance(payload, dict) else None) or settings.nvidia_model

    body = {
        "id": f"mock-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return JSONResponse(status_code=200, content=body)


@router.post("/gemini/v1beta/models/{model}:generateContent")
async def gemini_generate_content(model: str, request: Request) -> Response:
    """Gemini REST generateContent. The real API takes the API key as a
    ?key= query parameter rather than a bearer header; this route accepts
    either (or neither) and never validates it — the caller only needs a
    deterministic, schema-valid response or a documented failure mode. The
    decoded payload is a JSON object, as a string, in
    candidates[0].content.parts[0].text."""
    failure = await _maybe_fail(request, gemini=True)
    if failure is not None:
        return failure

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    content = _resolve_task_and_content(payload)

    body = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": json.dumps(content)}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 0,
            "candidatesTokenCount": 0,
            "totalTokenCount": 0,
        },
        "modelVersion": model,
    }
    return JSONResponse(status_code=200, content=body)
