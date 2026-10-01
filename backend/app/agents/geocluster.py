"""GeoCluster agent — ward resolution and corroboration context.

The geometry is computed in PostGIS; AI is used only to summarise the resulting
picture in human terms. A ward boundary is a polygon containment test, so it is
never left to a model.

This node also performs one deterministic priority adjustment: a corroborated
cluster raises urgency. Six people photographing the same pothole is stronger
evidence than one, and the escalation is recorded as its own audit event rather
than hidden inside the classifier.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.factory import infer
from app.ai.prompts import build_cluster_summary
from app.config import settings
from app.core.enums import ComplaintStatus, EventType, Priority
from app.core.events import append_event
from app.core.geo import nearby_complaints, resolve_ward
from app.models import Complaint

AGENT_NAME = "geocluster"

PRIORITY_LADDER = [Priority.P4.value, Priority.P3.value, Priority.P2.value, Priority.P1.value]


def _escalate(priority: str) -> str:
    idx = PRIORITY_LADDER.index(priority)
    return PRIORITY_LADDER[min(idx + 1, len(PRIORITY_LADDER) - 1)]


def run(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    def _work() -> AgentResult:
        ward = resolve_ward(db, complaint.latitude, complaint.longitude)
        neighbours = nearby_complaints(
            db,
            complaint.latitude,
            complaint.longitude,
            exclude_complaint_id=complaint.id,
            same_category=complaint.category.value if complaint.category else None,
        )
        nearby_count = len(neighbours)
        is_hotspot = nearby_count >= settings.hotspot_min_complaints

        summary_result = infer(
            build_cluster_summary(
                nearby_count=nearby_count,
                ward_name=ward.ward_name,
                category=complaint.category.value if complaint.category else None,
                radius_meters=settings.cluster_radius_meters,
                is_hotspot=is_hotspot,
                window_hours=settings.cluster_window_hours,
            )
        )

        return AgentResult(
            output={
                "ward_id": ward.ward_id,
                "ward_name": ward.ward_name,
                "ward_number": ward.ward_number,
                "zone": ward.zone,
                "containment_exact": ward.exact,
                "ward_distance_meters": None if ward.exact else ward.distance_meters,
                "nearby_count": nearby_count,
                "is_hotspot": is_hotspot,
                "nearby_references": [n.reference for n in neighbours[:10]],
                "summary": summary_result.data.get("summary", ""),
            },
            rationale=(
                f"PostGIS resolved ward "
                f"{'by containment' if ward.exact else 'by nearest-boundary fallback'}"
                f" ({ward.ward_name}); {nearby_count} corroborating complaint(s) "
                f"within {settings.cluster_radius_meters:.0f} m over "
                f"{settings.cluster_window_hours} h."
            ),
            confidence=1.0 if ward.exact else 0.7,
            provider=summary_result.provider,
            model=summary_result.model,
            is_ai=summary_result.is_ai,
            error=summary_result.error,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    complaint.ward_id = result.output.get("ward_id")
    complaint.nearby_count = int(result.output.get("nearby_count") or 0)
    complaint.is_hotspot = bool(result.output.get("is_hotspot"))
    complaint.cluster_summary = result.output.get("summary")
    complaint.status = ComplaintStatus.LOCATED

    append_event(
        db,
        complaint.id,
        EventType.LOCATED,
        f"Mapped to ward {result.output.get('ward_name')} with "
        f"{complaint.nearby_count} nearby complaint(s).",
        {
            "ward_id": complaint.ward_id,
            "ward_name": result.output.get("ward_name"),
            "containment_exact": result.output.get("containment_exact"),
            "nearby_count": complaint.nearby_count,
            "is_hotspot": complaint.is_hotspot,
        },
        actor=AGENT_NAME,
    )

    # Deterministic corroboration escalation.
    if complaint.is_hotspot and complaint.priority:
        before = complaint.priority.value
        after = _escalate(before)
        if after != before:
            complaint.priority = Priority(after)
            append_event(
                db,
                complaint.id,
                EventType.STATUS_CHANGED,
                f"Priority raised {before} -> {after}: "
                f"{complaint.nearby_count} corroborating reports meet the "
                f"hotspot threshold of {settings.hotspot_min_complaints}.",
                {"priority_before": before, "priority_after": after, "rule": "cluster_escalation"},
                actor=AGENT_NAME,
            )

    db.flush()
    return result


def refresh_cluster_counts(db: Session, limit: int = 1000) -> int:
    """Recompute ``nearby_count``/``is_hotspot`` for non-terminal complaints.

    Clustering is evaluated at intake, so the first report at a location
    legitimately records zero neighbours. Once four more arrive, that first
    complaint is part of a hotspot but its own row still says otherwise. This
    keeps the per-complaint snapshot coherent with the live analytics view.

    Called from the SLA sweep, so it runs on the same background cadence.
    """
    from sqlalchemy import select

    from app.core.enums import ComplaintStatus as _Status

    complaints = (
        db.execute(
            select(Complaint)
            .where(Complaint.status.notin_([_Status.REJECTED]))
            .order_by(Complaint.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    changed = 0
    for complaint in complaints:
        neighbours = nearby_complaints(
            db,
            complaint.latitude,
            complaint.longitude,
            exclude_complaint_id=complaint.id,
            same_category=complaint.category.value if complaint.category else None,
        )
        count = len(neighbours)
        hotspot = count >= settings.hotspot_min_complaints
        if complaint.nearby_count != count or complaint.is_hotspot != hotspot:
            complaint.nearby_count = count
            complaint.is_hotspot = hotspot
            changed += 1
    db.flush()
    return changed
