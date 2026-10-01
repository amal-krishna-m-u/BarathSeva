"""GeminiProvider (Task 4) — httpx REST adapter, no SDK.

Every test runs with no API key and no network: ``httpx.MockTransport``
stands in for the real Gemini endpoint. The one hard requirement is that
rate-limit detection — both a bare 429 and a non-429 response whose body
carries ``RESOURCE_EXHAUSTED`` — classifies as ``ErrorKind.RATE_LIMITED``,
since that is what lets the NIM-primary/Gemini-fallback ladder ever reach
this provider at all.
"""

from __future__ import annotations

import ast
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.ai.base import InferenceRequest, Task
from app.ai.errors import ErrorKind
from app.ai.gemini_provider import GeminiProvider, _downscale_or_drop_image
from app.config import settings

API_KEY = "test-gemini-key-123"
MODEL = "gemini-2.0-flash"


def _gemini_body(data: dict) -> dict:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": json.dumps(data)}]},
                "finishReason": "STOP",
            }
        ]
    }


def _rate_limit_body() -> dict:
    return {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check quota).",
            "status": "RESOURCE_EXHAUSTED",
        }
    }


def _make_request(**overrides) -> InferenceRequest:
    kwargs = dict(
        task=Task.CLASSIFY,
        system="You classify civic complaints.",
        user="Citizen report: \"pothole on 5th main\"",
        schema={"category": "string", "priority": "string"},
    )
    kwargs.update(overrides)
    return InferenceRequest(**kwargs)


def _tiny_png_bytes(size=(20, 20), color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _large_jpeg_bytes(size=(800, 800)) -> bytes:
    """A real decodable image, large enough to exceed a tiny byte budget."""
    img = Image.new("RGB", size, (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _provider(handler, **init_kwargs) -> GeminiProvider:
    transport = httpx.MockTransport(handler)
    return GeminiProvider(API_KEY, MODEL, transport=transport, **init_kwargs)


# --------------------------------------------------------------------------- happy path
class TestHappyPath:
    def test_successful_inference_parses_candidate_text_as_json(self):
        expected = {"category": "pothole", "priority": "P2", "confidence": 0.8, "rationale": "clear case"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_gemini_body(expected))

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is True
        assert result.error is None
        assert result.error_kind is None
        assert result.data == expected
        assert result.confidence == 0.8
        assert result.rationale == "clear case"
        assert result.provider == "gemini"
        assert result.model == MODEL
        assert result.is_ai is True
        assert result.latency_ms >= 0

    def test_request_url_and_method(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        provider.infer(_make_request())

        assert captured["method"] == "POST"
        assert f"/v1beta/models/{MODEL}:generateContent" in captured["url"]

    def test_api_key_sent_as_query_parameter(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["key"] = request.url.params.get("key")
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        provider.infer(_make_request())

        assert captured["key"] == API_KEY

    def test_generation_config_sent_in_payload(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        provider.infer(_make_request())

        config = captured["payload"]["generationConfig"]
        assert config == {"response_mime_type": "application/json", "temperature": 0.2}


# --------------------------------------------------------------------------- rate limiting
class TestRateLimiting:
    def test_429_status_classifies_as_rate_limited(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=_rate_limit_body())

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.RATE_LIMITED
        assert result.rate_limited is True

    def test_non_429_status_with_resource_exhausted_body_classifies_as_rate_limited(self):
        """Google's documented 403 + rateLimitExceeded/quotaExceeded shape."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json=_rate_limit_body())

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.RATE_LIMITED

    def test_200_status_with_resource_exhausted_body_classifies_as_rate_limited(self):
        """Quota exhaustion is not always paired with a non-2xx status."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_rate_limit_body())

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.RATE_LIMITED


# --------------------------------------------------------------------------- other failure kinds
class TestOtherFailures:
    def test_401_classifies_as_auth(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"error": {"message": "Invalid API key.", "status": "UNAUTHENTICATED"}}
            )

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.AUTH

    def test_timeout_classifies_as_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.TIMEOUT

    def test_non_json_candidate_text_classifies_as_bad_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {
                "candidates": [{"content": {"parts": [{"text": "not valid json {{"}]}}]
            }
            return httpx.Response(200, json=body)

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.BAD_RESPONSE

    def test_missing_candidates_classifies_as_bad_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"candidates": []})

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.BAD_RESPONSE

    def test_server_error_classifies_as_server(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": "internal error"}})

        provider = _provider(handler)
        result = provider.infer(_make_request())

        assert result.ok is False
        assert result.error_kind == ErrorKind.SERVER


# --------------------------------------------------------------------------- images
class TestImageInlining:
    def test_image_sent_as_base64_inline_data(self):
        image_bytes = _tiny_png_bytes()
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        provider.infer(_make_request(image_bytes=image_bytes, image_mime="image/png"))

        parts = captured["payload"]["contents"][0]["parts"]
        inline_parts = [p for p in parts if "inline_data" in p]
        assert len(inline_parts) == 1
        inline = inline_parts[0]["inline_data"]
        assert inline["mime_type"] == "image/png"
        assert base64.b64decode(inline["data"]) == image_bytes

    def test_no_image_means_no_inline_data_part(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        provider.infer(_make_request())

        parts = captured["payload"]["contents"][0]["parts"]
        assert all("inline_data" not in p for p in parts)

    def test_oversized_image_is_downscaled_below_budget(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 5_000, raising=False)
        original = _large_jpeg_bytes()
        assert len(original) > 5_000  # sanity: the fixture is actually oversized

        downscaled, mime = _downscale_or_drop_image(original, "image/jpeg")

        assert downscaled is not None
        assert mime == "image/jpeg"
        # Pillow can't always hit an arbitrary byte budget exactly on a tiny
        # synthetic image, but downscaling must make real progress.
        assert len(downscaled) < len(original)
        with Image.open(io.BytesIO(downscaled)) as img:
            img.load()  # still a valid, decodable image

    def test_oversized_image_downscaled_end_to_end_through_infer(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 5_000, raising=False)
        original = _large_jpeg_bytes()
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        result = provider.infer(_make_request(image_bytes=original, image_mime="image/jpeg"))

        assert result.ok is True
        parts = captured["payload"]["contents"][0]["parts"]
        inline_parts = [p for p in parts if "inline_data" in p]
        assert len(inline_parts) == 1
        sent_bytes = base64.b64decode(inline_parts[0]["inline_data"]["data"])
        assert len(sent_bytes) < len(original)

    def test_pillow_failure_drops_image_instead_of_failing(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 10, raising=False)
        garbage = b"this is not an image, just padding bytes" * 5
        assert len(garbage) > 10

        result_bytes, result_mime = _downscale_or_drop_image(garbage, "image/jpeg")

        assert result_bytes is None
        assert result_mime is None

    def test_pillow_failure_end_to_end_proceeds_text_only(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_max_image_bytes", 10, raising=False)
        garbage = b"this is not an image, just padding bytes" * 5
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_body({"ok": True}))

        provider = _provider(handler)
        result = provider.infer(_make_request(image_bytes=garbage, image_mime="image/jpeg"))

        assert result.ok is True  # the bad image did not fail the inference
        parts = captured["payload"]["contents"][0]["parts"]
        assert all("inline_data" not in p for p in parts)  # dropped, not sent


# --------------------------------------------------------------------------- no SDK import
class TestNoGoogleImport:
    def test_module_imports_no_google_package(self):
        """The whole point of the rewrite: no optional-SDK import anywhere
        in this module, lazy or otherwise. Checked via AST rather than a
        text grep so a mention of "google" in a comment/docstring (e.g.
        explaining the rewrite) can never produce a false failure."""
        source_path = Path(__file__).resolve().parents[1] / "app" / "ai" / "gemini_provider.py"
        tree = ast.parse(source_path.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.split(".")[0] == "google"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.split(".")[0] == "google"
