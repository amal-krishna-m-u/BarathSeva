"""OpenAI adapter. Imported lazily so the SDK is an optional dependency."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from app.ai.base import InferenceRequest, InferenceResult
from app.config import settings


class OpenAIProvider:
    name = "openai"
    is_ai = True

    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI  # imported here: optional dependency

        self._client = OpenAI(api_key=api_key)
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
            return InferenceResult(
                data={},
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                is_ai=True,
                error=f"{type(exc).__name__}: {exc}",
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
