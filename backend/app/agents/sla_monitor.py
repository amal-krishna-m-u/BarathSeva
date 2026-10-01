"""SLA monitor — deadlines and escalation, entirely deterministic.

``start`` attaches the policy deadline at dispatch. ``sweep`` is what the
background worker runs: it re-reads complaint state, marks breaches and raises
escalation levels. An SLA deadline is arithmetic, so no model is involved.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.core.enums import ComplaintStatus, EventType
from app.core.events import append_event
from app.core.sla import apply_sla, evaluate
from app.models import Complaint, utcnow

AGENT_NAME = "sla_monitor"

#: Escalation levels are capped so a stuck complaint does not escalate forever.
MAX_ESCALATION_LEVEL = 3


def start(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    """Compute and attach the SLA deadline from policy (runs after dispatch)."""

    def _work() -> AgentResult:
        computation = apply_sla(db, complaint, start=complaint.dispatched_at)
        return AgentResult.deterministic(
            output={
                "sla_policy_id": computation.policy_id,
                "due_at": computation.due_at.isoformat() if computation.due_at else None,
                "escalate_at": (
                    computation.escalate_at.isoformat()
                    if computation.escalate_at
                    else None
                ),
                "resolution_hours": computation.resolution_hours,
                "escalation_hours": computation.escalation_hours,
            },
            rationale=computation.reason,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    if result.output.get("due_at"):
        append_event(
            db,
            complaint.id,
            EventType.SLA_STARTED,
            f"SLA started: due {result.output['due_at']} "
            f"({result.output['resolution_hours']}h).",
            result.output,
            actor=AGENT_NAME,
        )
    db.flush()
    return result


def sweep(db: Session, limit: int = 500) -> dict[str, Any]:
    """Find overdue complaints, mark breaches, raise escalations.

    Idempotent: re-running does not double-escalate, because each level is only
    raised when the elapsed time justifies a level the complaint has not
    reached yet.
    """
    now = utcnow()
    candidates = (
        db.execute(
            select(Complaint)
            .where(
                Complaint.sla_due_at.isnot(None),
                Complaint.status.notin_(
                    [ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED]
                ),
            )
            .order_by(Complaint.sla_due_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    newly_breached: list[str] = []
    newly_escalated: list[str] = []

    for complaint in candidates:
        status = evaluate(complaint, now)

        if status.breached and not complaint.sla_breached:
            complaint.sla_breached = True
            newly_breached.append(complaint.reference)
            append_event(
                db,
                complaint.id,
                EventType.SLA_BREACHED,
                f"SLA breached: {status.overdue_seconds // 3600}h past the "
                f"{complaint.sla_due_at.isoformat()} deadline.",
                {
                    "overdue_seconds": status.overdue_seconds,
                    "due_at": complaint.sla_due_at.isoformat(),
                },
                actor=AGENT_NAME,
            )

        if status.escalation_due:
            # One level per full escalation window elapsed, capped.
            target = 1
            if complaint.sla_escalate_at and complaint.sla_due_at:
                window = (complaint.sla_due_at - complaint.sla_escalate_at).total_seconds()
                if window > 0:
                    overshoot = (now - complaint.sla_escalate_at).total_seconds()
                    target = min(
                        MAX_ESCALATION_LEVEL, max(1, int(overshoot // window) + 1)
                    )
            if target > complaint.escalation_level:
                before = complaint.escalation_level
                complaint.escalation_level = target
                if complaint.status != ComplaintStatus.ESCALATED:
                    complaint.status = ComplaintStatus.ESCALATED
                newly_escalated.append(complaint.reference)
                append_event(
                    db,
                    complaint.id,
                    EventType.ESCALATED,
                    f"Escalation level {before} -> {target} "
                    f"(no resolution past the escalation point).",
                    {"level_before": before, "level_after": target},
                    actor=AGENT_NAME,
                )

    # Keep per-complaint cluster snapshots coherent with the live analytics.
    from app.agents.geocluster import refresh_cluster_counts

    clusters_updated = refresh_cluster_counts(db)

    db.commit()
    return {
        "checked": len(candidates),
        "newly_breached": newly_breached,
        "newly_escalated": newly_escalated,
        "clusters_updated": clusters_updated,
        "swept_at": now.isoformat(),
    }
