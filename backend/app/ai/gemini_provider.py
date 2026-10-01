"""Google Gemini adapter — plain ``httpx`` REST, no vendor SDK.

``google-genai`` is an optional dependency that is not installed in this
environment, so going through the SDK meant this file failed at import and
had zero test coverage: ``ai_provider=gemini`` silently degraded to the stub
via ``factory.py``'s try/except, with nothing exercising the degrade path on
purpose. Speaking the Gemini REST API (``POST
.../v1beta/models/{model}:generateContent``) directly over ``httpx`` removes
that dependency risk entirely, and — the decisive reason — turns rate-limit
detection into a plain HTTP status code and response body instead of an
SDK-specific exception type. That is exactly the shape ``app.ai.errors``
already understands, and exactly what the primary-is-rate-limited fallback
ladder needs: Gemini is consulted only when NVIDIA NIM (the primary) reports
a 429, so this module's own 429/``RESOURCE_EXHAUSTED`` handling has to be
correct for the ladder to ever reach Gemini at all.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional

import httpx

from app.ai.base import InferenceRequest, InferenceResult
from app.ai.errors import ErrorKind, classify_exception, is_rate_limit_body
from app.ai.images import prepare_image_for_inference
from app.config import settings

logger = logging.getLogger(__name__)

#: Failures worth retrying on a different model. A bad key (AUTH) or a
#: malformed reply (BAD_RESPONSE) fails identically everywhere, so retrying
#: those only burns the request budget.
#:
#: RATE_LIMITED is included because Gemini meters per MODEL: gemini-3.8-flash
#: returning 429 says nothing about gemini-3.1-flash-lite's quota. This does
#: NOT hide the signal from the cross-provider ladder -- when every model in
#: the rotation is rate limited, the final result returned is still the last
#: one, so its RATE_LIMITED kind survives.
_RETRYABLE_KINDS = {
    ErrorKind.SERVER,
    ErrorKind.TIMEOUT,
    ErrorKind.NETWORK,
    ErrorKind.RATE_LIMITED,
}

#: Total attempts including the configured model. Bounded because every
#: attempt spends the full request timeout on the intake path.
_MAX_MODEL_ATTEMPTS = 3


class GeminiProvider:
    name = "gemini"
    is_ai = True

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = settings.gemini_base_url.rstrip("/")
        #: ``transport`` lets tests inject ``httpx.MockTransport`` with no
        #: network and no API key. ``None`` uses httpx's real transport.
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(settings.ai_request_timeout_seconds),
        )

    def infer(self, request: InferenceRequest) -> InferenceResult:
        models = self._model_rotation()
        result = None
        for index, model in enumerate(models):
            result = self._attempt(request, model)
            if result.ok or result.error_kind not in _RETRYABLE_KINDS:
                return result
            if index + 1 < len(models):
                logger.warning(
                    "gemini model %s failed on task %s (%s); retrying with %s",
                    model,
                    request.task,
                    result.error_kind,
                    models[index + 1],
                )
        return result

    def _model_rotation(self) -> list[str]:
        """The configured model first, then alternates, de-duplicated.

        Google's free tier returns 503 "experiencing high demand" and read
        timeouts on a per-model basis, and which model is healthy rotates
        minute to minute. Without this, a transient 503 on one model becomes a
        held complaint: the image gate fails closed, so an unanswered check
        sends a perfectly genuine pothole to a human reviewer. Trying the next
        model costs a few seconds and converts most of those holds back into
        real answers.
        """
        rotation = [self.model]
        for alt in (settings.gemini_fallback_models or "").split(","):
            alt = alt.strip()
            if alt and alt not in rotation:
                rotation.append(alt)
        return rotation[:_MAX_MODEL_ATTEMPTS]

    def _attempt(self, request: InferenceRequest, model: str) -> InferenceResult:
        started = time.perf_counter()

        image_bytes, image_mime = request.image_bytes, request.image_mime
        if image_bytes:
            # Shared with NvidiaProvider: one size guard for every vision
            # provider, and it reports the media type it actually produced.
            image_bytes, image_mime = prepare_image_for_inference(
                image_bytes, settings.ai_max_image_bytes, image_mime
            )

        payload = self._build_payload(request, image_bytes, image_mime)
        url = f"{self._base_url}/v1beta/models/{model}:generateContent"

        try:
            response = self._client.post(url, params={"key": self._api_key}, json=payload)
            response.raise_for_status()
        except Exception as exc:
            return self._error_result(started, exc)

        if is_rate_limit_body(response.text):
            # Google does not always pair RESOURCE_EXHAUSTED with a 429
            # status; a 2xx response can still carry it in the body. This
            # catches that case directly since raise_for_status() above
            # never raises on a 2xx.
            return self._error_result(
                started,
                RuntimeError(f"rate limit signalled in response body: {response.text[:200]}"),
                forced_kind=ErrorKind.RATE_LIMITED,
            )

        try:
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            return self._error_result(started, ValueError(f"malformed Gemini response: {exc}"))

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return self._error_result(started, exc)

        return InferenceResult(
            data=data,
            rationale=str(data.get("rationale", "")),
            confidence=float(data.get("confidence", 0.5) or 0.5),
            provider=self.name,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            is_ai=True,
        )

    def _build_payload(
        self,
        request: InferenceRequest,
        image_bytes: Optional[bytes],
        image_mime: Optional[str],
    ) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [
            {
                "text": (
                    f"{request.system}\n\n{request.user}\n\n"
                    f"Return JSON with exactly these fields:\n"
                    f"{json.dumps(request.schema, indent=2)}"
                )
            }
        ]
        if image_bytes:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image_mime or "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode(),
                    }
                }
            )
        return {
            "contents": [{"parts": parts}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.2},
        }

    def _error_result(
        self,
        started: float,
        exc: Exception,
        *,
        forced_kind: Optional[str] = None,
    ) -> InferenceResult:
        kind = forced_kind or classify_exception(exc)
        return InferenceResult(
            data={},
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            is_ai=True,
            error=f"{type(exc).__name__}: {exc}",
            error_kind=kind,
        )
