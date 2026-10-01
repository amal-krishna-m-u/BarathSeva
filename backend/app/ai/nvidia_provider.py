"""NVIDIA NIM adapter for ``moonshotai/kimi-k3`` — the headline provider.

NIM exposes an OpenAI-compatible ``/chat/completions`` endpoint, so this
mirrors ``app/ai/openai_provider.py``'s request shape (``response_format``,
``temperature``, ``max_tokens``, the system/user message pair, and the
``image_url`` data-URI content part) deliberately: the prompts in
``app/ai/prompts.py`` are provider-agnostic, and keeping the wire shape
identical is what lets them carry over unchanged. Unlike the OpenAI and
Gemini adapters, this one speaks plain ``httpx`` rather than a vendor SDK —
NIM needs no SDK of its own, and a bare ``httpx.Client`` is what makes the
``transport`` seam below possible (tests inject ``httpx.MockTransport`` and
never touch the network).

Every failure — HTTP status, timeout, transport error, or a response that
simply isn't the JSON object the schema promised — is classified through
``app.ai.errors`` rather than re-implemented here. The one classification
that matters most to the platform: a NIM ``429`` must come back as
``error_kind == ErrorKind.RATE_LIMITED``, because that is the only signal
that triggers the Gemini fallback a later task builds on top of this.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional

import httpx

from app.ai.base import InferenceRequest, InferenceResult
from app.ai.errors import classify_exception
from app.ai.images import prepare_image_for_inference
from app.config import settings


class NvidiaProvider:
    name = "nvidia"
    is_ai = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        #: Explicit Timeout (not a bare float passed straight to the client)
        #: so connect/read/write/pool all inherit the same bound — intake is
        #: synchronous and must never hang on a third party (GC4).
        #: ``transport`` is the test seam: pass ``httpx.MockTransport`` to
        #: exercise every response/failure shape with no network (GC7).
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

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
            # Oversized images are downscaled; an unreadable image is dropped
            # entirely rather than failing the whole inference (D4).
            image_bytes = prepare_image_for_inference(
                request.image_bytes, settings.ai_max_image_bytes
            )
            if image_bytes:
                b64 = base64.b64encode(image_bytes).decode()
                mime = request.image_mime or "image/jpeg"
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
                    }
                )

        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
        }

        try:
            response = self._client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            try:
                raw = payload["choices"][0]["message"]["content"] or "{}"
            except (KeyError, IndexError, TypeError) as exc:
                # Reshaped as ValueError so classify_exception's ValueError
                # branch recognises it as BAD_RESPONSE, same as a JSON parse
                # failure below — the response simply wasn't usable.
                raise ValueError(f"unexpected response shape: {exc}") from exc
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("content JSON was not an object")
        except Exception as exc:
            return InferenceResult(
                data={},
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                is_ai=True,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=classify_exception(exc),
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
