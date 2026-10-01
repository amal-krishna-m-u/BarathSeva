"""The mock government HTTP surface: dialect shapes, idempotency, failure
injection, and the production gate.

``app.integrations.mock_gov`` was always in-process only, so these tests are
the first proof that the three department dialects survive an actual HTTP
round trip and that ticket ids minted over HTTP genuinely agree with
``MockGovernmentGateway`` — the real in-process class, imported here, never
a hardcoded literal.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mock_gov_api import reset_mock_gov_state
from app.config import Settings, settings
from app.integrations.mock_gov import MockGovernmentGateway, _deterministic_suffix
from app.main import register_mock_routers


@pytest.fixture(autouse=True)
def _clean_mock_gov_state():
    reset_mock_gov_state()
    yield
    reset_mock_gov_state()


def _payload(reference: str = "CMP-0001", **overrides: Any) -> dict[str, Any]:
    body = {
        "reference": reference,
        "category": "pothole",
        "priority": "high",
        "ward_name": "Koramangala",
        "description": "Large pothole near the main signal.",
        "latitude": 12.9352,
        "longitude": 77.6245,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------
# Dialect shapes
# --------------------------------------------------------------------------


def test_bbmp_dialect_shape(client):
    response = client.post("/mock/gov/bbmp/grievance", json=_payload("BBMP-REF-1"))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status", "grievance_id", "zone_office", "received_on", "echo"}
    assert body["status"] == "ACCEPTED"
    assert body["grievance_id"].startswith("BBMP-")
    assert body["zone_office"] == "Koramangala"
    assert body["echo"]["source_reference"] == "BBMP-REF-1"
    assert body["echo"]["category"] == "pothole"


def test_bwssb_dialect_shape(client):
    response = client.post("/mock/gov/bwssb/complaint", json=_payload("BWSSB-REF-1"))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"result", "complaint_no", "subdivision", "logged_at", "echo"}
    assert body["result"] == "OK"
    assert body["complaint_no"].startswith("BWSSB/")
    assert body["subdivision"] == "Koramangala"


def test_bescom_dialect_shape(client):
    response = client.post("/mock/gov/bescom/ticket", json=_payload("BESCOM-REF-1"))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"code", "ticket_ref", "section_office", "timestamp", "echo"}
    assert body["code"] == 200
    assert body["ticket_ref"].startswith("BESCOM")
    assert body["section_office"] == "Koramangala"
    assert isinstance(body["timestamp"], int)


# --------------------------------------------------------------------------
# Ticket id parity with the in-process gateway — the core contract.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,dept,id_field,salt,prefix_check",
    [
        ("/mock/gov/bbmp/grievance", "BBMP", "grievance_id", "bbmp", "BBMP-"),
        ("/mock/gov/bwssb/complaint", "BWSSB", "complaint_no", "bwssb", "BWSSB/"),
        ("/mock/gov/bescom/ticket", "BESCOM", "ticket_ref", "bescom", "BESCOM"),
    ],
)
def test_ticket_id_matches_in_process_gateway(client, path, dept, id_field, salt, prefix_check):
    reference = f"PARITY-{dept}"
    response = client.post(path, json=_payload(reference))
    assert response.status_code == 200
    http_id = response.json()[id_field]

    # The real in-process gateway, a fresh instance, never a hardcoded
    # string — if mock_gov_api.py ever started reimplementing id generation
    # instead of reusing _deterministic_suffix, this would catch the drift.
    in_process = MockGovernmentGateway().create_ticket(
        dept,
        reference=reference,
        category="pothole",
        priority="high",
        ward_name="Koramangala",
        description="Large pothole near the main signal.",
        latitude=12.9352,
        longitude=77.6245,
    )
    assert http_id == in_process.ticket_id

    # And directly against the imported suffix function itself.
    assert _deterministic_suffix(reference, salt).upper() in http_id.upper()


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_repeat_post_is_idempotent(client):
    first = client.post("/mock/gov/bbmp/grievance", json=_payload("IDEMP-1"))
    second = client.post("/mock/gov/bbmp/grievance", json=_payload("IDEMP-1", description="different text now"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["grievance_id"] == second.json()["grievance_id"]
    # The second POST's different description must NOT have overwritten the
    # stored ticket — the echo proves which payload actually "won".
    assert second.json()["echo"]["description"] == "Large pothole near the main signal."


def test_different_references_get_different_tickets(client):
    first = client.post("/mock/gov/bbmp/grievance", json=_payload("IDEMP-A"))
    second = client.post("/mock/gov/bbmp/grievance", json=_payload("IDEMP-B"))
    assert first.json()["grievance_id"] != second.json()["grievance_id"]


def test_idempotency_is_per_department(client):
    """The same reference used against two different departments must mint
    two independent tickets, not collide on a shared key."""
    bbmp = client.post("/mock/gov/bbmp/grievance", json=_payload("SHARED-REF"))
    bwssb = client.post("/mock/gov/bwssb/complaint", json=_payload("SHARED-REF"))
    assert bbmp.status_code == 200
    assert bwssb.status_code == 200
    assert bbmp.json()["grievance_id"] != bwssb.json()["complaint_no"]


# --------------------------------------------------------------------------
# _control endpoint — failing_departments reachable over HTTP
# --------------------------------------------------------------------------


def test_control_get_defaults_empty(client):
    response = client.get("/mock/gov/_control")
    assert response.status_code == 200
    assert response.json() == {"failing_departments": [], "latency_ms": 0}


def test_control_configured_failure_isolates_department(client):
    set_resp = client.post("/mock/gov/_control", json={"failing_departments": ["BWSSB"], "latency_ms": 0})
    assert set_resp.status_code == 200
    assert set_resp.json()["failing_departments"] == ["BWSSB"]

    get_resp = client.get("/mock/gov/_control")
    assert get_resp.json()["failing_departments"] == ["BWSSB"]

    failing = client.post("/mock/gov/bwssb/complaint", json=_payload("CTRL-1"))
    assert failing.status_code == 503
    assert "BWSSB" in failing.json()["error"]

    healthy = client.post("/mock/gov/bbmp/grievance", json=_payload("CTRL-2"))
    assert healthy.status_code == 200


def test_control_replaces_not_merges(client):
    client.post("/mock/gov/_control", json={"failing_departments": ["BWSSB"], "latency_ms": 0})
    client.post("/mock/gov/_control", json={"failing_departments": ["BESCOM"], "latency_ms": 0})

    state = client.get("/mock/gov/_control").json()
    assert state["failing_departments"] == ["BESCOM"]

    # BWSSB recovered because the second call replaced, not merged.
    recovered = client.post("/mock/gov/bwssb/complaint", json=_payload("CTRL-3"))
    assert recovered.status_code == 200

    still_failing = client.post("/mock/gov/bescom/ticket", json=_payload("CTRL-4"))
    assert still_failing.status_code == 503


# --------------------------------------------------------------------------
# X-Mock-Failure header, each documented mode
# --------------------------------------------------------------------------


def test_header_unavailable(client):
    response = client.post(
        "/mock/gov/bbmp/grievance",
        json=_payload("HDR-UNAVAIL"),
        headers={"X-Mock-Failure": "unavailable"},
    )
    assert response.status_code == 503
    assert "BBMP" in response.json()["error"]
    # And it must not have minted a ticket a later unheadered retry would
    # idempotently replay.
    retry = client.post("/mock/gov/bbmp/grievance", json=_payload("HDR-UNAVAIL"))
    assert retry.status_code == 200


def test_header_rate_limit(client):
    response = client.post(
        "/mock/gov/bescom/ticket",
        json=_payload("HDR-RATE"),
        headers={"X-Mock-Failure": "rate_limit"},
    )
    assert response.status_code == 429
    body = response.json()
    assert "retry_after_seconds" in body


def test_header_malformed(client):
    response = client.post(
        "/mock/gov/bwssb/complaint",
        json=_payload("HDR-MALFORMED"),
        headers={"X-Mock-Failure": "malformed"},
    )
    assert response.status_code == 200
    with pytest.raises(ValueError):
        response.json()


def test_header_slow_is_fast_and_succeeds(client):
    """Must never actually sleep for anything close to a second — this test
    would time out the whole suite if the default delay were not small."""
    import time

    started = time.monotonic()
    response = client.post(
        "/mock/gov/bbmp/grievance",
        json=_payload("HDR-SLOW"),
        headers={"X-Mock-Failure": "slow"},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"
    assert elapsed < 1.0


def test_header_slow_respects_control_latency(client):
    import time

    client.post("/mock/gov/_control", json={"failing_departments": [], "latency_ms": 10})
    started = time.monotonic()
    response = client.post(
        "/mock/gov/bbmp/grievance",
        json=_payload("HDR-SLOW-2"),
        headers={"X-Mock-Failure": "slow"},
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed >= 0.01
    assert elapsed < 1.0


def test_header_unknown_mode_is_400(client):
    response = client.post(
        "/mock/gov/bbmp/grievance",
        json=_payload("HDR-BOGUS"),
        headers={"X-Mock-Failure": "not-a-real-mode"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# GET .../ticket/{id} read-back
# --------------------------------------------------------------------------


def test_get_ticket_reads_back_created_ticket(client):
    created = client.post("/mock/gov/bescom/ticket", json=_payload("GET-1")).json()
    ticket_id = created["ticket_ref"]

    response = client.get(f"/mock/gov/BESCOM/ticket/{ticket_id}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_ticket_unknown_id_is_404(client):
    response = client.get("/mock/gov/BBMP/ticket/NOPE-404")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Production gate
# --------------------------------------------------------------------------


def test_mock_gov_routes_absent_in_production():
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
        response = test_client.post("/mock/gov/bbmp/grievance", json=_payload("PROD-CHECK"))

    assert response.status_code == 404


def test_mock_gov_routes_present_when_mock_apis_enabled():
    dev_settings = Settings(_env_file=None, environment="development", enable_mock_apis=True)
    app = FastAPI()
    register_mock_routers(app, settings_obj=dev_settings)

    with TestClient(app) as test_client:
        response = test_client.post("/mock/gov/bbmp/grievance", json=_payload("DEV-CHECK"))

    assert response.status_code == 200


def test_mock_gov_routes_absent_when_flag_disabled_even_in_development():
    disabled_settings = Settings(environment="development", enable_mock_apis=False, _env_file=None)
    app = FastAPI()
    register_mock_routers(app, settings_obj=disabled_settings)

    with TestClient(app) as test_client:
        response = test_client.post("/mock/gov/bbmp/grievance", json=_payload("FLAG-CHECK"))

    assert response.status_code == 404
