"""OpenAI adapter. Imported lazily so the SDK is an optional dependency."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional

from app.ai.base import InferenceRequest, InferenceResult
from app.ai.errors import ErrorKind, classify_exception, classify_http_status, is_rate_limit_body
from app.config import settings


class OpenAIProvider:
    name = "openai"
    is_ai = True

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None) -> None:
        from openai import OpenAI  # imported here: optional dependency

        #: base_url lets this adapter point at an OpenAI-compatible endpoint
        #: other than api.openai.com (e.g. the mock LLM in tests) without a
        #: second provider class. Optional and defaults to the SDK's own
        #: default when unset, so existing callers are unaffected.
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)
        self.model = model

    def infer(self, request: InferenceRequest) -> InferenceResult:
        started = time.perf_counter()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{request.user}\n\nReturn JSON with exactly these fields:\n"
                    f"{json.dumps(request.schema, indent=2)}"
                ),
            }
        ]
        if request.image_bytes:
            b64 = base64.b64encode(request.image_bytes).decode()
            mime = request.image_mime or "image/jpeg"
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
                }
            )

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": content},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
        except Exception as exc:
            # The openai SDK raises its own exception hierarchy (RateLimitError,
            # AuthenticationError, ...), never httpx's — classify_exception's
            # isinstance checks never fire on them. Those SDK exceptions do
            # carry the HTTP status as `.status_code` when the failure came
            # from the API itself, so that is classified directly (mirroring
            # classify_exception's own AUTH/OTHER-can-upgrade-to-RATE_LIMITED,
            # SERVER-cannot rule); anything without a status_code (e.g. a
            # local JSON decode failure) still goes through classify_exception.
            status_code = getattr(exc, "status_code", None)
            if status_code is not None:
                error_kind = classify_http_status(status_code)
                if error_kind not in (ErrorKind.RATE_LIMITED, ErrorKind.SERVER) and is_rate_limit_body(
                    str(exc)
                ):
                    error_kind = ErrorKind.RATE_LIMITED
            else:
                error_kind = classify_exception(exc)
            return InferenceResult(
                data={},
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                is_ai=True,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=error_kind,
            )

        return InferenceResult(
            data=data,
            rationale=str(data.get("rationale", "")),
            confidence=float(data.get("confidence", 0.5) or 0.5),
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            is_ai=True,
        )
