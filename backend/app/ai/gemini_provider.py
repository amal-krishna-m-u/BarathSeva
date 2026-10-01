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
import time
from typing import Any, Optional

import httpx

from app.ai.base import InferenceRequest, InferenceResult
from app.ai.errors import ErrorKind, classify_exception, is_rate_limit_body
from app.ai.images import prepare_image_for_inference
from app.config import settings


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
        started = time.perf_counter()

        image_bytes, image_mime = request.image_bytes, request.image_mime
        if image_bytes:
            # Shared with NvidiaProvider: one size guard for every vision
            # provider, and it reports the media type it actually produced.
            image_bytes, image_mime = prepare_image_for_inference(
                image_bytes, settings.ai_max_image_bytes, image_mime
            )

        payload = self._build_payload(request, image_bytes, image_mime)
        url = f"{self._base_url}/v1beta/models/{self.model}:generateContent"

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
            model=self.model,
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
