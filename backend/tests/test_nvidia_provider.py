"""NvidiaProvider (NIM / Kimi K3) — the headline inference provider.

Every test runs against ``httpx.MockTransport``: no network, no API key. The
single most important assertion in this file is that a 429 from NIM comes
back as ``error_kind == ErrorKind.RATE_LIMITED`` — that is the only signal
the fallback ladder (a later task) watches for, and nothing downstream will
ever fire without it.
"""

from __future__ import annotations

import base64
import io
import json
import os

import httpx
import pytest

from app.ai.base import InferenceRequest, Task
from app.ai.errors import ErrorKind
from app.ai.images import prepare_image_for_inference
from app.ai.nvidia_provider import NvidiaProvider
from app.config import settings

API_KEY = "test-nim-key"
MODEL = "moonshotai/kimi-k3"
BASE_URL = "https://integrate.api.nvidia.com/v1"


def _provider(transport: httpx.MockTransport) -> NvidiaProvider:
    return NvidiaProvider(
        api_key=API_KEY,
        model=MODEL,
        base_url=BASE_URL,
        timeout=5.0,
        transport=transport,
    )


def _request(image_bytes: bytes | None = None) -> InferenceRequest:
    return InferenceRequest(
        task=Task.CLASSIFY,
        system="You classify civic complaints.",
        user="Citizen report: pothole on MG Road.",
        schema={"category": "string", "confidence": "number"},
        image_bytes=image_bytes,
    )


def _chat_completion_body(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _noise_jpeg(min_bytes: int) -> bytes:
    """A real JPEG that is guaranteed to exceed ``min_bytes`` — noise barely
    compresses, so a small image at high quality is enough; grows the image
    until the threshold is actually crossed so the test is not sensitive to
    the exact Pillow/libjpeg version in use."""
    from PIL import Image

    size = 64
    while True:
        img = Image.frombytes("RGB", (size, size), os.urandom(size * size * 3))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=100)
        data = buf.getvalue()
        if len(data) > min_bytes:
            return data
        size += 64


# --------------------------------------------------------------------- happy path
class TestHappyPath:
    def test_parses_content_json_into_data_and_sets_is_ai_true(self):
        payload = {"category": "pothole", "confidence": 0.82, "rationale": "clear photo"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_completion_body(json.dumps(payload)))

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request())

        assert result.ok is True
        assert result.error is None
        assert result.error_kind is None
        assert result.is_ai is True
        assert result.provider == "nvidia"
        assert result.model == MODEL
        assert result.data == payload
        assert result.rationale == "clear photo"
        assert result.confidence == 0.82


# --------------------------------------------------------------------- failure shapes
class TestFailureClassification:
    def _infer_against_status(self, status: int, body: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body or {"error": {"message": "boom"}})

        provider = _provider(httpx.MockTransport(handler))
        return provider.infer(_request())

    def test_429_is_rate_limited(self):
        """The assertion that matters most: a real 429 from NIM must surface
        as RATE_LIMITED, or the fallback ladder never fires."""
        result = self._infer_against_status(
            429,
            {"error": {"message": "Rate limit reached for requests.", "code": "rate_limit_exceeded"}},
        )
        assert result.ok is False
        assert result.error_kind == ErrorKind.RATE_LIMITED
        assert result.rate_limited is True
        assert result.data == {}
        assert result.is_ai is True

    def test_401_is_auth(self):
        result = self._infer_against_status(401, {"error": {"message": "Invalid API key"}})
        assert result.error_kind == ErrorKind.AUTH
        assert result.rate_limited is False

    def test_500_is_server(self):
        result = self._infer_against_status(500, {"error": {"message": "internal error"}})
        assert result.error_kind == ErrorKind.SERVER

    def test_timeout_is_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request())

        assert result.error_kind == ErrorKind.TIMEOUT
        assert result.rate_limited is False

    def test_non_json_content_is_bad_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_completion_body("this is not JSON"))

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request())

        assert result.error_kind == ErrorKind.BAD_RESPONSE

    def test_malformed_response_shape_is_bad_response(self):
        """choices missing entirely — not just bad content JSON."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "x", "choices": []})

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request())

        assert result.error_kind == ErrorKind.BAD_RESPONSE


# --------------------------------------------------------------------- request shape
class TestRequestShape:
    def test_bearer_header_and_exact_model_id_are_sent(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion_body(json.dumps({"ok": True})))

        provider = _provider(httpx.MockTransport(handler))
        provider.infer(_request())

        assert captured["headers"]["authorization"] == f"Bearer {API_KEY}"
        assert captured["body"]["model"] == "moonshotai/kimi-k3"
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["temperature"] == 0.2
        assert captured["body"]["max_tokens"] == 600

    def test_posts_to_chat_completions_under_base_url(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json=_chat_completion_body(json.dumps({"ok": True})))

        provider = _provider(httpx.MockTransport(handler))
        provider.infer(_request())

        assert captured["url"] == f"{BASE_URL}/chat/completions"

    def test_body_is_parseable_by_mock_llm_collect_text(self):
        """Cross-task contract: app.api.mock_llm._collect_text walks the
        request body looking for strings under "text"/"content" keys. Proves
        our outgoing shape is reachable by that walk without importing the
        (untouched) mock_llm module into this test."""

        captured: dict = {}

        def capturing_handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion_body(json.dumps({"ok": True})))

        provider = _provider(httpx.MockTransport(capturing_handler))
        provider.infer(_request())

        acc: list[str] = []

        def collect_text(obj, acc):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in ("text", "content") and isinstance(value, str) and value:
                        acc.append(value)
                    else:
                        collect_text(value, acc)
            elif isinstance(obj, list):
                for item in obj:
                    collect_text(item, acc)

        collect_text(captured["body"], acc)
        combined = "\n\n".join(acc)
        assert "You classify civic complaints." in combined
        assert "Citizen report: pothole on MG Road." in combined


# --------------------------------------------------------------------- image handling
class TestImageHandling:
    def test_oversized_image_is_downscaled_and_request_still_sends(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 20_000, raising=False)
        oversized = _noise_jpeg(20_000)
        assert len(oversized) > 20_000

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion_body(json.dumps({"ok": True})))

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request(image_bytes=oversized))

        assert result.ok is True
        user_content = captured["body"]["messages"][1]["content"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        assert len(image_parts) == 1
        data_url = image_parts[0]["image_url"]["url"]
        assert data_url.startswith("data:image/jpeg;base64,")
        sent_bytes = base64.b64decode(data_url.split(",", 1)[1])
        assert len(sent_bytes) <= 20_000
        assert len(sent_bytes) < len(oversized)

    def test_pillow_failure_drops_image_and_proceeds_text_only(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 10, raising=False)
        garbage = b"not a real image, but longer than ten bytes"
        assert len(garbage) > 10

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion_body(json.dumps({"ok": True})))

        provider = _provider(httpx.MockTransport(handler))
        result = provider.infer(_request(image_bytes=garbage))

        assert result.ok is True  # the call itself must still succeed
        user_content = captured["body"]["messages"][1]["content"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        assert image_parts == []  # image silently dropped, not sent


# --------------------------------------------------------------------- images.py unit tests
class TestPrepareImageForInference:
    def test_none_in_none_out(self):
        assert prepare_image_for_inference(None, 1000) is None

    def test_small_image_returned_unchanged(self):
        small = b"x" * 50
        assert prepare_image_for_inference(small, 1000) is small

    def test_oversized_real_image_is_downscaled_under_budget(self):
        big = _noise_jpeg(20_000)
        out = prepare_image_for_inference(big, 20_000)
        assert out is not None
        assert len(out) <= 20_000

    def test_corrupt_bytes_over_budget_returns_none(self):
        garbage = b"\x00" * 2000
        assert prepare_image_for_inference(garbage, 1000) is None
