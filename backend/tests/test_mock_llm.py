"""The mock LLM endpoint: both wire shapes, failure injection, and the
production gate.

The machine running this suite has no API key for any provider, so these
tests are the only proof that the mock server speaks exactly what the real
NVIDIA/NIM and Gemini providers expect, and that its decisions genuinely
agree with StubProvider rather than a separately hand-rolled rule set.
Wherever a test asserts "matches the stub", it calls the real StubProvider
itself to get the expected value — never a hardcoded literal — so the
assertion breaks if the mock and the stub ever drift apart.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import prompts
from app.ai.base import InferenceRequest, Task
from app.ai.stub import StubProvider
from app.api.mock_llm import reset_failure_counts
from app.config import Settings, settings
from app.main import register_mock_routers

CHAT_URL = "/mock/llm/v1/chat/completions"


def _gemini_url(model: str | None = None) -> str:
    return f"/mock/llm/gemini/v1beta/models/{model or settings.gemini_model}:generateContent"


@pytest.fixture(autouse=True)
def _clean_failure_counters():
    """Mock-failure counters are module-level state; keep each test isolated
    from whatever the previous one injected."""
    reset_failure_counts()
    yield
    reset_failure_counts()


def _openai_payload(req: InferenceRequest) -> dict[str, Any]:
    """Build the OpenAI chat-completions request the real NVIDIA/NIM
    provider (Task 3) would send for this InferenceRequest."""
    if req.image_bytes:
        b64 = base64.b64encode(req.image_bytes).decode()
        user_content: Any = [
            {"type": "text", "text": req.user},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{req.image_mime or 'image/jpeg'};base64,{b64}"},
            },
        ]
    else:
        user_content = req.user
    return {
        "model": settings.nvidia_model,
        "messages": [
            {"role": "system", "content": req.system},
            {"role": "user", "content": user_content},
        ],
    }


def _gemini_payload(req: InferenceRequest) -> dict[str, Any]:
    """Build the Gemini generateContent request the real Gemini fallback
    provider (Task 4) would send for this InferenceRequest."""
    parts: list[dict[str, Any]] = [{"text": req.user}]
    if req.image_bytes:
        parts.append(
            {
                "inline_data": {
                    "mime_type": req.image_mime or "image/jpeg",
                    "data": base64.b64encode(req.image_bytes).decode(),
                }
            }
        )
    return {
        "systemInstruction": {"parts": [{"text": req.system}]},
        "contents": [{"role": "user", "parts": parts}],
    }


def _expected(req: InferenceRequest) -> dict[str, Any]:
    """What the real StubProvider decides for this request's context — the
    mock's response content must agree with this."""
    result = StubProvider().infer(req)
    data = dict(result.data)
    if req.task == Task.VERIFY:
        data.pop("matched_phrases", None)
    return data


# --------------------------------------------------------------------------
# Guard: pin the literal prompt markers mock_llm.py's verify/classify
# regexes depend on.
#
# Unlike cluster_summary/social/resolution — which recover their whole
# context dict from a single embedded JSON blob — _verify_context() and
# _classify_context() in app/api/mock_llm.py recover context by matching
# exact substrings out of the prose app.ai.prompts.build_verify() and
# build_classify() produce ('Citizen report: "', 'Photograph attached:',
# 'Ward:', 'Corroborating reports nearby:'). If someone rewords those
# prompts (a legitimate thing to do — prompt wording is a model-quality
# lever, not a mock's concern), the regexes silently stop matching and
# the mock falls back to an empty/default context instead of raising.
#
# That failure mode is NOT fully silent today: test_openai_verify_matches_stub
# and test_openai_classify_matches_stub build their *expected* value from the
# real StubProvider fed the TRUE context, while the mock's actual response is
# built from the regex-RECOVERED context — so a prompt reword that changes
# the stub's verdict (e.g. is_civic_issue flips True -> False) does surface
# as a failing assertion elsewhere in this file. But that protection is
# incidental: it only holds for fixtures whose verdict actually changes under
# degraded parsing, and the failure it produces reads as "mock disagrees with
# stub" — true, but it does not name the actual cause. This test exists so a
# prompt wording change fails HERE first, with a message that says exactly
# which marker moved, instead of forcing a developer to rediscover that
# mock_llm.py parses prompt prose at all.
# --------------------------------------------------------------------------


def test_prompt_markers_mock_llm_depends_on_are_present():
    """Pins the exact substrings app/api/mock_llm.py's _verify_context() and
    _classify_context() regex-match out of app.ai.prompts' prose. These two
    tasks (alone among the five) are NOT recovered from an embedded JSON
    blob, so they are only as robust as this wording staying put. If this
    test fails, app/ai/prompts.py was reworded — go update the corresponding
    regex in app/api/mock_llm.py, not this test."""
    verify_req = prompts.build_verify(
        description="A pothole on MG Road",
        has_photo=True,
        image_readable=True,
        authenticity_score=0.5,
        authenticity_outcome="AUTO_ACCEPT",
        signals=[],
    )
    verify_text = verify_req.system + "\n\n" + verify_req.user

    classify_req = prompts.build_classify(
        description="A pothole on MG Road",
        nearby_count=2,
        ward_name="Koramangala",
        category_hint=None,
    )
    classify_text = classify_req.system + "\n\n" + classify_req.user

    # _verify_context() markers
    assert 'Citizen report: "' in verify_text
    assert "Photograph attached:" in verify_text

    # _classify_context() markers
    assert 'Citizen report: "' in classify_text
    assert "Ward:" in classify_text
    assert "Corroborating reports nearby:" in classify_text


# --------------------------------------------------------------------------
# Happy path: each task, OpenAI shape, verdicts agree with StubProvider.
# --------------------------------------------------------------------------


def test_openai_verify_matches_stub(client):
    req = prompts.build_verify(
        description="There is a large pothole on MG Road that caused a bike accident",
        has_photo=True,
        image_readable=True,
        authenticity_score=0.8,
        authenticity_outcome="AUTO_ACCEPT",
        signals=[{"code": "test_signal", "severity": "low", "detail": "a detail"}],
    )
    expected = _expected(req)

    response = client.post(CHAT_URL, json=_openai_payload(req))

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"]
    message = body["choices"][0]["message"]
    assert message["role"] == "assistant"
    content = json.loads(message["content"])  # must be a JSON *string* in content

    assert content["is_civic_issue"] == expected["is_civic_issue"]
    assert content["evidence_sufficient"] == expected["evidence_sufficient"]
    assert content["category_hint"] == expected["category_hint"]
    assert isinstance(content["is_civic_issue"], bool)
    assert isinstance(content["evidence_sufficient"], bool)
    assert isinstance(content["confidence"], (int, float))
    assert isinstance(content["rationale"], str) and content["rationale"]


def test_openai_classify_matches_stub(client):
    req = prompts.build_classify(
        description="Massive pothole causing multiple accidents near the school",
        nearby_count=5,
        ward_name="Koramangala",
        category_hint=None,
    )
    expected = _expected(req)

    response = client.post(CHAT_URL, json=_openai_payload(req))

    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content["category"] == expected["category"]
    assert content["priority"] == expected["priority"]
    assert content["severity_note"] == expected["severity_note"]
    assert isinstance(content["confidence"], (int, float))


def test_openai_cluster_summary_matches_stub(client):
    req = prompts.build_cluster_summary(
        nearby_count=5,
        ward_name="Koramangala",
        category="POTHOLE",
        radius_meters=150.0,
        is_hotspot=True,
        window_hours=72,
    )
    expected = _expected(req)

    response = client.post(CHAT_URL, json=_openai_payload(req))

    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content["summary"] == expected["summary"]


def test_openai_social_matches_stub(client):
    req = prompts.build_social(
        category="POTHOLE",
        ward_name="Koramangala",
        reference="BS-1001",
        department="BBMP",
        nearby_count=5,
        priority="P2",
        sla_breached=False,
        sla_due_at_human="tomorrow 5pm",
    )
    expected = _expected(req)

    response = client.post(CHAT_URL, json=_openai_payload(req))

    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content["content"] == expected["content"]


def test_openai_resolution_matches_stub(client):
    req = prompts.build_resolution(
        category="POTHOLE",
        ward_name="Koramangala",
        reference="BS-1001",
        department="BBMP",
        resolution_note="Pothole filled with asphalt.",
        hours_taken=12,
        external_ticket_id="BBMP-ABC123",
    )
    expected = _expected(req)

    response = client.post(CHAT_URL, json=_openai_payload(req))

    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content["message"] == expected["message"]


# --------------------------------------------------------------------------
# Happy path: Gemini shape.
# --------------------------------------------------------------------------


def test_gemini_verify_matches_stub(client):
    req = prompts.build_verify(
        description="Overflowing sewage near the bus stop, terrible smell",
        has_photo=False,
        image_readable=True,
        authenticity_score=0.0,
        authenticity_outcome="HUMAN_REVIEW",
        signals=[],
    )
    expected = _expected(req)

    response = client.post(
        _gemini_url() + "?key=unused-test-key", json=_gemini_payload(req)
    )

    assert response.status_code == 200
    body = response.json()
    candidate = body["candidates"][0]
    assert candidate["content"]["role"] == "model"
    text = candidate["content"]["parts"][0]["text"]
    content = json.loads(text)
    assert content["is_civic_issue"] == expected["is_civic_issue"]
    assert content["category_hint"] == expected["category_hint"]
    assert isinstance(content["is_civic_issue"], bool)


def test_gemini_bearer_auth_header_accepted_without_validation(client):
    """The real Gemini API takes ?key=; this route must also tolerate a
    bearer header, and never validates either one."""
    req = prompts.build_cluster_summary(
        nearby_count=0, ward_name="Jayanagar", category="GARBAGE",
        radius_meters=150.0, is_hotspot=False, window_hours=72,
    )
    response = client.post(
        _gemini_url(),
        json=_gemini_payload(req),
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Image / vision parts must not choke the mock.
# --------------------------------------------------------------------------

FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0not-a-real-jpeg-just-bytes"


def test_openai_accepts_image_part(client):
    req = prompts.build_verify(
        description="Streetlight has been out for a week on 4th cross",
        has_photo=True,
        image_readable=True,
        authenticity_score=0.6,
        authenticity_outcome="AUTO_ACCEPT",
        signals=[],
        image_bytes=FAKE_IMAGE_BYTES,
        image_mime="image/jpeg",
    )
    response = client.post(CHAT_URL, json=_openai_payload(req))
    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert "is_civic_issue" in content


def test_gemini_accepts_image_part(client):
    req = prompts.build_verify(
        description="Streetlight has been out for a week on 4th cross",
        has_photo=True,
        image_readable=True,
        authenticity_score=0.6,
        authenticity_outcome="AUTO_ACCEPT",
        signals=[],
        image_bytes=FAKE_IMAGE_BYTES,
        image_mime="image/jpeg",
    )
    response = client.post(_gemini_url(), json=_gemini_payload(req))
    assert response.status_code == 200
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    content = json.loads(text)
    assert "is_civic_issue" in content


# --------------------------------------------------------------------------
# Failure injection.
# --------------------------------------------------------------------------


def test_rate_limit_openai(client):
    response = client.post(
        CHAT_URL, json={"messages": []}, headers={"X-Mock-Failure": "rate_limit"}
    )
    assert response.status_code == 429
    assert "error" in response.json()


def test_rate_limit_gemini_body_contains_resource_exhausted(client):
    response = client.post(
        _gemini_url(), json={"contents": []}, headers={"X-Mock-Failure": "rate_limit"}
    )
    assert response.status_code == 429
    assert "RESOURCE_EXHAUSTED" in response.text


def test_auth_failure(client):
    response = client.post(
        CHAT_URL, json={"messages": []}, headers={"X-Mock-Failure": "auth"}
    )
    assert response.status_code == 401


def test_server_failure(client):
    response = client.post(
        CHAT_URL, json={"messages": []}, headers={"X-Mock-Failure": "server"}
    )
    assert response.status_code == 500


def test_garbage_failure_returns_200_with_unparseable_body(client):
    response = client.post(
        CHAT_URL, json={"messages": []}, headers={"X-Mock-Failure": "garbage"}
    )
    assert response.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        json.loads(response.text)


def test_unknown_failure_mode_is_rejected_cleanly(client):
    response = client.post(
        CHAT_URL, json={"messages": []}, headers={"X-Mock-Failure": "not_a_mode"}
    )
    assert response.status_code == 400


def test_failure_count_fails_exactly_n_then_succeeds(client):
    headers = {"X-Mock-Failure": "server", "X-Mock-Failure-Count": "2"}
    payload = {"messages": []}

    first = client.post(CHAT_URL, json=payload, headers=headers)
    second = client.post(CHAT_URL, json=payload, headers=headers)
    third = client.post(CHAT_URL, json=payload, headers=headers)

    assert first.status_code == 500
    assert second.status_code == 500
    assert third.status_code == 200


def test_failure_counters_are_independent_per_mode(client):
    """Count N against rate_limit must not consume the count for auth —
    each failure mode gets its own counter."""
    rate_limit_headers = {"X-Mock-Failure": "rate_limit", "X-Mock-Failure-Count": "1"}
    auth_headers = {"X-Mock-Failure": "auth", "X-Mock-Failure-Count": "1"}
    payload = {"messages": []}

    assert client.post(CHAT_URL, json=payload, headers=rate_limit_headers).status_code == 429
    # rate_limit's single allotted failure is used up; this call should succeed...
    assert client.post(CHAT_URL, json=payload, headers=rate_limit_headers).status_code == 200
    # ...but auth's counter is untouched and still fails once.
    assert client.post(CHAT_URL, json=payload, headers=auth_headers).status_code == 401
    assert client.post(CHAT_URL, json=payload, headers=auth_headers).status_code == 200


# --------------------------------------------------------------------------
# Timeout injection needs a real socket: FastAPI's TestClient talks to the
# ASGI app in-process, so an httpx client-side timeout never actually fires
# against it (there is no real I/O to interrupt). A short-lived uvicorn
# server makes the delay and the timeout real, the way Task 3/4's provider
# clients would experience it.
# --------------------------------------------------------------------------


class _LiveServer:
    def __init__(self, app: FastAPI) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.server.started, "mock live server failed to start"
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def test_timeout_header_triggers_a_real_client_side_timeout():
    """A client with a 0.1s timeout against an endpoint told to delay 0.3s
    must see a real timeout — proving the ladder's timeout branch is
    reachable from a test without ever waiting out the real 20s default.

    Deliberately NOT the full app.main app: that has a DB-touching startup
    event and shares the global SQLAlchemy engine/pool. A throwaway
    FastAPI() carrying only the mock router keeps this server's background
    thread from ever competing for a connection with the rest of the suite,
    even if shutdown is slightly delayed."""
    req = prompts.build_classify(
        description="pothole", nearby_count=0, ward_name=None, category_hint=None
    )
    mock_only_app = FastAPI()
    register_mock_routers(mock_only_app, settings_obj=settings)
    with _LiveServer(mock_only_app) as base_url:
        with pytest.raises(httpx.TimeoutException):
            httpx.post(
                f"{base_url}{CHAT_URL}",
                json=_openai_payload(req),
                headers={"X-Mock-Failure": "timeout", "X-Mock-Delay-Seconds": "0.3"},
                timeout=0.1,
            )


# --------------------------------------------------------------------------
# Production gate: routes must not exist at all outside development/staging,
# even if enable_mock_apis is left true. Built as a fresh FastAPI() per
# app.main's register_mock_routers contract, so this needs no reload of
# app.main and cannot leak into any other test.
# --------------------------------------------------------------------------


def test_mock_routes_absent_in_production():
    prod_settings = Settings(
        _env_file=None,
        environment="production",
        enable_mock_apis=True,
        secret_key="a-real-rotated-secret",
        admin_api_key="a-real-rotated-admin-key",
    )
    app = FastAPI()
    register_mock_routers(app, settings_obj=prod_settings)

    with TestClient(app) as test_client:
        response = test_client.post(CHAT_URL, json={"messages": []})

    assert response.status_code == 404


def test_mock_routes_present_when_mock_apis_enabled():
    dev_settings = Settings(
        _env_file=None, environment="development", enable_mock_apis=True
    )
    app = FastAPI()
    register_mock_routers(app, settings_obj=dev_settings)

    with TestClient(app) as test_client:
        response = test_client.post(CHAT_URL, json={"messages": []})

    assert response.status_code == 200


def test_mock_routes_absent_when_flag_disabled_even_in_development():
    disabled_settings = Settings(
        _env_file=None, environment="development", enable_mock_apis=False
    )
    app = FastAPI()
    register_mock_routers(app, settings_obj=disabled_settings)

    with TestClient(app) as test_client:
        response = test_client.post(CHAT_URL, json={"messages": []})

    assert response.status_code == 404
