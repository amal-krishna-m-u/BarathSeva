"""The LangGraph pipeline and the HTTP surface, end to end.

These run the real graph against the real database — no node is stubbed —
because the thing worth testing is that a citizen message actually traverses
evidence → verification → classification → ward → dispatch → SLA.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai.base import InferenceRequest, Task
from app.ai.stub import StubProvider
from app.core.enums import AuthenticityOutcome, ComplaintStatus
from app.core.security import issue_capture_token
from app.models import AgentRun, Complaint, SocialPost
from app.services.intake import IntakeRequest, submit_complaint
from tests.conftest import INDIRANAGAR, JAYANAGAR, KORAMANGALA, MUMBAI


def submit(db, citizen, photo, description, location, **overrides):
    issued = issue_capture_token(db, citizen.id, "dev-test", *location)
    db.commit()
    payload = {
        "description": description,
        "latitude": location[0],
        "longitude": location[1],
        "channel": "web",
        "declared_source": "camera",
        "capture_token": issued.token,
        "image_bytes": photo(*location, seed=overrides.pop("seed", 1)),
        "image_mime": "image/jpeg",
        "gps_accuracy_meters": 8.0,
        "reporter_phone": citizen.phone,
    }
    payload.update(overrides)
    return submit_complaint(db, IntakeRequest(**payload))


# --------------------------------------------------------------- stub provider
class TestStubProvider:
    def setup_method(self):
        self.provider = StubProvider()

    def run(self, task, context):
        return self.provider.infer(
            InferenceRequest(task=task, system="", user="", schema={}, context=context)
        )

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Large pothole near 5th block", "POTHOLE"),
            ("Water gushing from a burst pipeline", "PIPELINE_BURST"),
            ("Sewage overflowing from the manhole", "SEWAGE"),
            ("Street light not working since 3 days", "STREETLIGHT"),
            ("No electricity since morning, transformer sparking", "POWER_OUTAGE"),
            ("Garbage not collected for a week", "GARBAGE"),
            ("Road is water logged and flooded", "DRAINAGE"),
        ],
    )
    def test_classification_matches_category(self, text, expected):
        assert self.run(Task.CLASSIFY, {"description": text}).data["category"] == expected

    def test_safety_language_raises_priority(self):
        calm = self.run(Task.CLASSIFY, {"description": "pothole on the road"})
        urgent = self.run(
            Task.CLASSIFY,
            {"description": "large deep pothole, a child was injured near the school"},
        )
        ladder = ["P4", "P3", "P2", "P1"]
        assert ladder.index(urgent.data["priority"]) > ladder.index(calm.data["priority"])

    def test_corroboration_raises_priority(self):
        alone = self.run(Task.CLASSIFY, {"description": "pothole", "nearby_count": 0})
        crowd = self.run(Task.CLASSIFY, {"description": "pothole", "nearby_count": 9})
        ladder = ["P4", "P3", "P2", "P1"]
        assert ladder.index(crowd.data["priority"]) > ladder.index(alone.data["priority"])

    def test_verifier_rejects_non_civic_noise(self):
        result = self.run(
            Task.VERIFY,
            {"description": "hello testing this app", "has_photo": False},
        )
        assert result.data["is_civic_issue"] is False

    def test_verifier_accepts_a_real_report(self):
        result = self.run(
            Task.VERIFY,
            {
                "description": "Large pothole near Koramangala 5th Block",
                "has_photo": True,
                "authenticity_score": 1.0,
            },
        )
        assert result.data["is_civic_issue"] and result.data["evidence_sufficient"]

    def test_unknown_task_reports_an_error_rather_than_guessing(self):
        assert self.run("not_a_task", {}).error.startswith("unsupported_task")

    def test_provider_is_honestly_labelled_non_ai(self):
        assert self.provider.is_ai is False


# ------------------------------------------------------------------- pipeline
class TestIntakePipeline:
    def test_clean_report_traverses_every_node(self, db, citizen, photo):
        result = submit(
            db,
            citizen,
            photo,
            "Large pothole near Koramangala 5th Block, very deep",
            KORAMANGALA,
        )
        assert result.accepted
        assert result.workflow_state["trace"] == [
            "evidence_gate",
            "verifier",
            "classifier",
            "geocluster",
            "dispatcher",
            "sla_monitor",
            "social_amplifier",
        ]

        complaint = db.get(Complaint, result.complaint_id)
        assert complaint.status is ComplaintStatus.DISPATCHED
        assert complaint.category.value == "POTHOLE"
        assert complaint.ward.name == "Koramangala"
        assert complaint.department.code == "BBMP"
        assert complaint.external_ticket_id.startswith("BBMP-")
        assert complaint.sla_due_at is not None

    def test_every_node_writes_an_agent_run(self, db, citizen, photo):
        result = submit(db, citizen, photo, "Pothole on the main road here", KORAMANGALA)
        runs = db.query(AgentRun).filter_by(complaint_id=result.complaint_id).all()
        assert {run.agent_name for run in runs} == {
            "evidence_gate",
            "verifier",
            "classifier",
            "geocluster",
            "dispatcher",
            "sla_monitor",
            "social_amplifier",
        }
        assert all(run.latency_ms is not None for run in runs)

    def test_hard_fail_halts_before_verification(self, db, citizen, photo):
        result = submit_complaint(
            db,
            IntakeRequest(
                description="Pothole on the highway",
                latitude=MUMBAI[0],
                longitude=MUMBAI[1],
                capture_token=None,
                declared_source="camera",
                image_bytes=photo(*MUMBAI, seed=31),
                image_mime="image/jpeg",
                reporter_phone=citizen.phone,
            ),
        )
        assert result.accepted is False
        assert result.authenticity_outcome == AuthenticityOutcome.HARD_FAIL.value
        # The graph never ran, but the complaint still exists for audit.
        complaint = db.get(Complaint, result.complaint_id)
        assert complaint.status is ComplaintStatus.REJECTED
        assert db.query(AgentRun).filter_by(complaint_id=complaint.id).count() == 0

    def test_rejected_report_still_gets_a_reference(self, db, citizen, photo):
        result = submit_complaint(
            db,
            IntakeRequest(
                description="Pothole",
                latitude=MUMBAI[0],
                longitude=MUMBAI[1],
                image_bytes=photo(*MUMBAI, seed=32),
                image_mime="image/jpeg",
                reporter_phone=citizen.phone,
            ),
        )
        assert result.reference.startswith("BRS-")

    def test_non_civic_noise_is_rejected_by_the_verifier(self, db, citizen, photo):
        result = submit(db, citizen, photo, "hello testing this app", KORAMANGALA, seed=33)
        complaint = db.get(Complaint, result.complaint_id)
        assert complaint.status is ComplaintStatus.REJECTED
        # Evidence was fine — it is the verifier that stopped it.
        assert result.authenticity_outcome == AuthenticityOutcome.AUTO_ACCEPT.value
        assert result.workflow_state["trace"] == ["evidence_gate", "verifier"]

    def test_corroboration_builds_a_hotspot_and_raises_priority(
        self, db, citizen, photo
    ):
        offsets = [0.0, 0.0002, -0.0002, 0.0003, -0.0003]
        last = None
        for index, offset in enumerate(offsets):
            last = submit(
                db,
                citizen,
                photo,
                f"Pothole on the service road, report {index}",
                (KORAMANGALA[0] + offset, KORAMANGALA[1] + offset),
                seed=40 + index,
            )
        complaint = db.get(Complaint, last.complaint_id)
        assert complaint.nearby_count >= 4
        assert complaint.is_hotspot is True
        # Cluster escalation moves P3 up a step.
        assert complaint.priority.value in {"P1", "P2"}

    def test_image_reuse_is_caught_across_complaints(self, db, citizen, photo):
        reused = photo(*KORAMANGALA, seed=55)
        submit(db, citizen, photo, "Pothole here", KORAMANGALA, image_bytes=reused)
        second = submit(
            db,
            citizen,
            photo,
            "Another pothole somewhere else",
            INDIRANAGAR,
            image_bytes=reused,
        )
        assert second.accepted is False
        # Assert on the signal code, not prose: an exact SHA-256 match and a
        # near-exact perceptual match produce different wording.
        codes = {signal["code"] for signal in second.evidence_signals}
        assert codes & {"image_reused_exact", "image_reused_near_exact"}

    @pytest.mark.parametrize(
        "description,location,department",
        [
            ("Water gushing from a burst pipeline on the road", INDIRANAGAR, "BWSSB"),
            ("No electricity since morning, transformer sparking", JAYANAGAR, "BESCOM"),
            ("Garbage not collected for a week near the gate", KORAMANGALA, "BBMP"),
        ],
    )
    def test_routing_reaches_the_right_agency(
        self, db, citizen, photo, description, location, department
    ):
        result = submit(db, citizen, photo, description, location, seed=hash(department) % 900)
        complaint = db.get(Complaint, result.complaint_id)
        assert complaint.department.code == department

    def test_social_post_generated_for_high_priority(self, db, citizen, photo):
        result = submit(
            db,
            citizen,
            photo,
            "No electricity in the whole area, transformer sparking, very dangerous",
            JAYANAGAR,
            seed=61,
        )
        posts = db.query(SocialPost).filter_by(complaint_id=result.complaint_id).all()
        assert len(posts) == 1
        assert posts[0].is_published is False  # never actually posted in the prototype

    def test_resolution_updates_trust_and_closes_the_loop(self, db, citizen, photo):
        from app.agents import resolution

        result = submit(db, citizen, photo, "Deep pothole on 5th block", KORAMANGALA, seed=70)
        complaint = db.get(Complaint, result.complaint_id)
        before = complaint.reporter.trust_score

        resolution.run(
            db, complaint, {}, resolution_note="Filled with hot-mix asphalt.",
            field_outcome="GENUINE_FIXED",
        )
        db.commit()

        assert complaint.status is ComplaintStatus.RESOLVED
        assert complaint.resolution_message
        assert complaint.reporter.trust_score > before

    def test_false_report_lowers_trust(self, db, citizen, photo):
        from app.agents import resolution

        result = submit(db, citizen, photo, "Pothole on the road here", KORAMANGALA, seed=71)
        complaint = db.get(Complaint, result.complaint_id)
        before = complaint.reporter.trust_score

        resolution.run(db, complaint, {}, field_outcome="NOT_FOUND")
        db.commit()
        assert complaint.reporter.trust_score < before


# ------------------------------------------------------------------------ API
class TestHttpApi:
    def test_health_reports_real_dependencies(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["checks"]["database"]["ok"] is True
        assert body["checks"]["government_apis"]["mode"] == "mock"

    def test_capture_token_then_submit_then_track(self, client, photo):
        token = client.post(
            "/api/capture-token",
            json={"device_id": "api-test", "latitude": KORAMANGALA[0],
                  "longitude": KORAMANGALA[1]},
        ).json()

        response = client.post(
            "/api/complaints",
            data={
                "description": "Large pothole near Koramangala 5th Block, very deep",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
                "declared_source": "camera",
                "capture_token": token["token"],
                "gps_accuracy_meters": "8",
            },
            files={"photo": ("e.jpg", photo(*KORAMANGALA, seed=81), "image/jpeg")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["department_code"] == "BBMP"

        tracked = client.get(f"/api/complaints/{body['reference']}").json()
        assert tracked["verified"] is True
        assert tracked["external_ticket_id"] == body["external_ticket_id"]
        assert len(tracked["events"]) >= 6

    def test_unknown_reference_is_404(self, client):
        assert client.get("/api/complaints/BRS-999999").status_code == 404

    def test_oversized_image_is_rejected(self, client):
        payload = b"\xff\xd8\xff" + b"0" * (13 * 1024 * 1024)
        response = client.post(
            "/api/complaints",
            data={
                "description": "Pothole on the road",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
            },
            files={"photo": ("big.jpg", payload, "image/jpeg")},
        )
        assert response.status_code == 413

    def test_unsupported_mime_is_rejected(self, client):
        response = client.post(
            "/api/complaints",
            data={
                "description": "Pothole on the road",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
            },
            files={"photo": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert response.status_code == 415

    def test_invalid_coordinates_rejected_by_validation(self, client):
        response = client.post(
            "/api/complaints",
            data={"description": "Pothole", "latitude": "999", "longitude": "77.6"},
        )
        assert response.status_code == 422

    def test_admin_endpoints_serve_the_command_center(self, as_super_admin, photo):
        token = as_super_admin.post(
            "/api/capture-token",
            json={"latitude": KORAMANGALA[0], "longitude": KORAMANGALA[1]},
        ).json()
        as_super_admin.post(
            "/api/complaints",
            data={
                "description": "Deep pothole near the junction, very dangerous",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
                "capture_token": token["token"],
                "gps_accuracy_meters": "8",
            },
            files={"photo": ("e.jpg", photo(*KORAMANGALA, seed=91), "image/jpeg")},
        )

        stats = as_super_admin.get("/api/admin/stats").json()
        assert stats["total"] >= 1
        assert stats["ai_is_real_model"] is False

        listing = as_super_admin.get("/api/admin/complaints").json()
        assert len(listing) >= 1

        detail = as_super_admin.get(f"/api/admin/complaints/{listing[0]['reference']}").json()
        assert detail["audit_chain_intact"] is True
        assert detail["evidence"]["signals"]
        assert detail["agent_runs"]

        wards = as_super_admin.get("/api/admin/wards").json()
        assert len(wards) == 18
        assert wards[0]["boundary"]["type"] == "MultiPolygon"

    def test_filters_narrow_the_feed(self, as_super_admin, photo):
        token = as_super_admin.post("/api/capture-token", json={}).json()
        as_super_admin.post(
            "/api/complaints",
            data={
                "description": "Garbage not collected for a week near the gate",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
                "capture_token": token["token"],
                "gps_accuracy_meters": "8",
            },
            files={"photo": ("e.jpg", photo(*KORAMANGALA, seed=92), "image/jpeg")},
        )
        garbage = as_super_admin.get("/api/admin/complaints?category=GARBAGE").json()
        assert all(item["category"] == "GARBAGE" for item in garbage)
        assert as_super_admin.get("/api/admin/complaints?category=POTHOLE").json() == []

    def test_sla_sweep_endpoint_runs(self, as_super_admin):
        body = as_super_admin.post("/api/admin/sla/sweep").json()
        assert "checked" in body and "clusters_updated" in body

    def test_resolve_endpoint_closes_the_complaint(self, as_super_admin, photo):
        token = as_super_admin.post("/api/capture-token", json={}).json()
        created = as_super_admin.post(
            "/api/complaints",
            data={
                "description": "Pothole near the bus stop, quite deep",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
                "capture_token": token["token"],
                "gps_accuracy_meters": "8",
            },
            files={"photo": ("e.jpg", photo(*KORAMANGALA, seed=93), "image/jpeg")},
        ).json()

        resolved = as_super_admin.post(
            f"/api/admin/complaints/{created['reference']}/resolve",
            json={"resolution_note": "Filled and levelled.", "field_outcome": "GENUINE_FIXED"},
        ).json()
        assert resolved["status"] == "RESOLVED"
        assert resolved["resolution_message"]

        again = as_super_admin.post(
            f"/api/admin/complaints/{created['reference']}/resolve",
            json={"resolution_note": "", "field_outcome": "GENUINE_FIXED"},
        )
        assert again.status_code == 409

    def test_invalid_field_outcome_rejected(self, as_super_admin, photo):
        token = as_super_admin.post("/api/capture-token", json={}).json()
        created = as_super_admin.post(
            "/api/complaints",
            data={
                "description": "Pothole near the market, deep",
                "latitude": str(KORAMANGALA[0]),
                "longitude": str(KORAMANGALA[1]),
                "capture_token": token["token"],
                "gps_accuracy_meters": "8",
            },
            files={"photo": ("e.jpg", photo(*KORAMANGALA, seed=94), "image/jpeg")},
        ).json()
        response = as_super_admin.post(
            f"/api/admin/complaints/{created['reference']}/resolve",
            json={"resolution_note": "", "field_outcome": "MADE_UP"},
        )
        assert response.status_code == 422

    def test_telegram_status_reports_disabled(self, client):
        assert client.get("/api/telegram/status").json()["enabled"] is False
