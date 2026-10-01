"""Deterministic layers: PostGIS geometry, SLA arithmetic, department routing.

These are the decisions the README says must never be left to a model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from geoalchemy2.elements import WKTElement

from app.core.departments import resolve_department
from app.core.enums import ComplaintCategory, ComplaintStatus, Priority
from app.core.events import append_event, verify_chain
from app.core.geo import (
    haversine_meters,
    hotspots,
    inside_city_envelope,
    nearby_complaints,
    resolve_ward,
)
from app.core.enums import EventType
from app.core.ids import next_complaint_reference
from app.core.sla import apply_sla, compute_sla, evaluate
from app.models import Complaint, ComplaintEvent, utcnow
from tests.conftest import INDIRANAGAR, JAYANAGAR, KORAMANGALA, MUMBAI


def make_complaint(db, lat, lon, *, category="POTHOLE", priority="P3", status=None):
    """Build a complaint the way the pipeline does, including ward assignment.

    ward_id matters: hotspot aggregation reports the modal ward of a cluster,
    so a fixture that skips it would report ward_name=None and make the
    clustering test look broken when it is not.
    """
    ward = resolve_ward(db, lat, lon)
    complaint = Complaint(
        reference=next_complaint_reference(db),
        description=f"test complaint at {lat},{lon}",
        status=status or ComplaintStatus.DISPATCHED,
        location=WKTElement(f"POINT({lon} {lat})", srid=4326),
        latitude=lat,
        longitude=lon,
        ward_id=ward.ward_id,
        category=ComplaintCategory(category),
        priority=Priority(priority),
    )
    db.add(complaint)
    db.flush()
    return complaint


class TestGeometry:
    def test_haversine_matches_known_distance(self):
        metres = haversine_meters(*KORAMANGALA, *INDIRANAGAR)
        assert 4000 < metres < 5000  # ~4.4 km

    def test_containment_resolves_exact_ward(self, db):
        match = resolve_ward(db, *KORAMANGALA)
        assert match.ward_name == "Koramangala"
        assert match.exact is True
        assert match.distance_meters == 0.0

    def test_gap_falls_back_to_nearest_ward(self, db):
        """Prototype envelopes do not tessellate, so gaps snap to the nearest
        ward and are flagged as approximate rather than silently exact."""
        match = resolve_ward(db, 12.9500, 77.6300)
        assert match.ward_id is not None
        assert match.exact is False
        assert match.distance_meters > 0

    def test_far_away_point_resolves_to_no_ward(self, db):
        assert resolve_ward(db, *MUMBAI).ward_id is None

    def test_city_envelope(self, db):
        assert inside_city_envelope(db, *KORAMANGALA) is True
        assert inside_city_envelope(db, *MUMBAI) is False

    def test_nearby_respects_radius(self, db):
        anchor = make_complaint(db, *KORAMANGALA)
        make_complaint(db, KORAMANGALA[0] + 0.0004, KORAMANGALA[1])  # ~44 m
        make_complaint(db, *JAYANAGAR)  # kilometres away
        db.commit()

        found = nearby_complaints(db, *KORAMANGALA, exclude_complaint_id=anchor.id)
        assert len(found) == 1
        assert found[0].distance_meters < 100

    def test_nearby_filters_by_category(self, db):
        anchor = make_complaint(db, *KORAMANGALA, category="POTHOLE")
        make_complaint(db, KORAMANGALA[0] + 0.0003, KORAMANGALA[1], category="GARBAGE")
        db.commit()
        found = nearby_complaints(
            db, *KORAMANGALA, exclude_complaint_id=anchor.id, same_category="POTHOLE"
        )
        assert found == []

    def test_nearby_excludes_rejected(self, db):
        anchor = make_complaint(db, *KORAMANGALA)
        make_complaint(
            db,
            KORAMANGALA[0] + 0.0003,
            KORAMANGALA[1],
            status=ComplaintStatus.REJECTED,
        )
        db.commit()
        assert nearby_complaints(db, *KORAMANGALA, exclude_complaint_id=anchor.id) == []

    def test_dbscan_clusters_dense_reports(self, db):
        for offset in range(5):
            make_complaint(
                db, KORAMANGALA[0] + offset * 0.0002, KORAMANGALA[1] + offset * 0.0001
            )
        make_complaint(db, *JAYANAGAR)  # isolated: must not form a cluster
        db.commit()

        clusters = hotspots(db, min_complaints=4)
        assert len(clusters) == 1
        assert clusters[0].complaint_count == 5
        assert clusters[0].ward_name == "Koramangala"


class TestDepartmentRouting:
    @pytest.mark.parametrize(
        "category,expected",
        [
            ("POTHOLE", "BBMP"),
            ("ROAD_DAMAGE", "BBMP"),
            ("GARBAGE", "BBMP"),
            ("STREETLIGHT", "BBMP"),
            ("DRAINAGE", "BBMP"),
            ("WATER_LEAK", "BWSSB"),
            ("PIPELINE_BURST", "BWSSB"),
            ("SEWAGE", "BWSSB"),
            ("POWER_OUTAGE", "BESCOM"),
        ],
    )
    def test_every_category_routes_deterministically(self, db, category, expected):
        department, reason = resolve_department(db, category)
        assert department.code == expected
        assert category in reason

    def test_routing_is_stable_across_calls(self, db):
        first = resolve_department(db, "WATER_LEAK")[0].code
        second = resolve_department(db, "WATER_LEAK")[0].code
        assert first == second

    def test_unknown_category_falls_back_to_municipal_default(self, db):
        department, reason = resolve_department(db, None)
        assert department.code == "BBMP"


class TestSLA:
    def test_policy_lookup_drives_the_deadline(self, db):
        computation = compute_sla(db, "POTHOLE", "P1")
        assert computation.resolution_hours == 24
        assert computation.escalation_hours == 14
        assert computation.due_at > utcnow()

    def test_higher_priority_is_never_slower(self, db):
        p1 = compute_sla(db, "WATER_LEAK", "P1").resolution_hours
        p3 = compute_sla(db, "WATER_LEAK", "P3").resolution_hours
        assert p1 < p3

    def test_missing_policy_yields_no_deadline(self, db):
        computation = compute_sla(db, None, None)
        assert computation.due_at is None
        assert "No SLA policy" in computation.reason

    def test_evaluate_reports_breach_and_remaining(self, db):
        complaint = make_complaint(db, *KORAMANGALA, priority="P2")
        apply_sla(db, complaint)
        db.commit()

        healthy = evaluate(complaint)
        assert healthy.breached is False
        assert healthy.remaining_seconds > 0

        complaint.sla_due_at = utcnow() - timedelta(hours=3)
        overdue = evaluate(complaint)
        assert overdue.breached is True
        assert overdue.overdue_seconds >= 3 * 3600 - 5

    def test_resolved_complaint_does_not_escalate(self, db):
        complaint = make_complaint(db, *KORAMANGALA, status=ComplaintStatus.RESOLVED)
        apply_sla(db, complaint)
        complaint.sla_escalate_at = utcnow() - timedelta(hours=5)
        db.commit()
        assert evaluate(complaint).escalation_due is False

    def test_sweep_is_idempotent(self, db):
        from app.agents.sla_monitor import sweep

        complaint = make_complaint(db, *KORAMANGALA, priority="P2")
        apply_sla(db, complaint)
        complaint.sla_due_at = utcnow() - timedelta(hours=2)
        complaint.sla_escalate_at = utcnow() - timedelta(hours=4)
        db.commit()

        first = sweep(db)
        assert complaint.reference in first["newly_breached"]

        second = sweep(db)
        assert second["newly_breached"] == []
        assert second["newly_escalated"] == []


class TestAuditChain:
    def test_chain_links_and_verifies(self, db):
        complaint = make_complaint(db, *KORAMANGALA)
        db.commit()
        for index in range(4):
            append_event(
                db, complaint.id, EventType.STATUS_CHANGED, f"step {index}", {"i": index}
            )
        db.commit()

        intact, broken = verify_chain(db, complaint.id)
        assert intact is True and broken is None

    def test_tampering_breaks_the_chain(self, db):
        complaint = make_complaint(db, *KORAMANGALA)
        db.commit()
        append_event(db, complaint.id, EventType.CREATED, "original message")
        append_event(db, complaint.id, EventType.VERIFIED, "second entry")
        db.commit()

        first = (
            db.query(ComplaintEvent)
            .filter_by(complaint_id=complaint.id)
            .order_by(ComplaintEvent.id)
            .first()
        )
        first.message = "rewritten after the fact"
        db.commit()

        intact, broken = verify_chain(db, complaint.id)
        assert intact is False
        assert broken == first.id
