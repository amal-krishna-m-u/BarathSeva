"""Google Gemini adapter. Imported lazily so the SDK is an optional dependency."""

from __future__ import annotations

import json
import time
from typing import Any

from app.ai.base import InferenceRequest, InferenceResult


class GeminiProvider:
    name = "gemini"
    is_ai = True

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai  # imported here: optional dependency

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def infer(self, request: InferenceRequest) -> InferenceResult:
        started = time.perf_counter()
        parts: list[Any] = [
            (
                f"{request.system}\n\n{request.user}\n\n"
                f"Return JSON with exactly these fields:\n"
                f"{json.dumps(request.schema, indent=2)}"
            )
        ]
        if request.image_bytes:
            parts.append(
                self._genai.types.Part.from_bytes(
                    data=request.image_bytes,
                    mime_type=request.image_mime or "image/jpeg",
                )
            )

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=parts,
                config={"response_mime_type": "application/json", "temperature": 0.2},
            )
            data = json.loads(response.text or "{}")
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
