"""Resolution update agent — closes the loop with the citizen.

Runs on the resolution transition rather than during intake. It also applies
the one piece of ground truth the system ever gets: the field outcome recorded
at closure, which adjusts the reporter's trust score (Layer 6).
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.factory import infer
from app.ai.prompts import build_resolution
from app.core.enums import ComplaintStatus, EventType
from app.core.events import append_event
from app.models import Complaint, utcnow

AGENT_NAME = "resolution_update"

TRUST_STEP_UP = 0.08
TRUST_STEP_DOWN = 0.20


def _hours_taken(complaint: Complaint) -> Optional[int]:
    if not complaint.created_at or not complaint.resolved_at:
        return None
    start = complaint.created_at
    end = complaint.resolved_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds() // 3600))


def run(
    db: Session,
    complaint: Complaint,
    state: dict[str, Any],
    resolution_note: str = "",
    field_outcome: str = "GENUINE_FIXED",
) -> AgentResult:
    complaint.resolved_at = complaint.resolved_at or utcnow()
    complaint.resolution_note = resolution_note or complaint.resolution_note
    complaint.field_outcome = field_outcome

    def _work() -> AgentResult:
        result = infer(
            build_resolution(
                reference=complaint.reference,
                category=complaint.category.value if complaint.category else None,
                ward_name=complaint.ward.name if complaint.ward else None,
                department=complaint.department.code if complaint.department else None,
                external_ticket_id=complaint.external_ticket_id,
                resolution_note=complaint.resolution_note,
                hours_taken=_hours_taken(complaint),
                field_outcome=field_outcome,
            )
        )
        return AgentResult(
            output={
                "message": str(result.data.get("message") or "").strip(),
                "field_outcome": field_outcome,
                "hours_taken": _hours_taken(complaint),
            },
            rationale=result.rationale or "Generated citizen resolution message.",
            confidence=result.confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            error=result.error,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    complaint.resolution_message = result.output.get("message")
    complaint.status = ComplaintStatus.RESOLVED

    # Field outcome is ground truth: feed it back into reporter reputation.
    reporter = complaint.reporter
    if reporter is not None:
        if field_outcome == "GENUINE_FIXED":
            reporter.reports_confirmed += 1
            reporter.trust_score = min(1.0, reporter.trust_score + TRUST_STEP_UP)
        elif field_outcome == "NOT_FOUND":
            reporter.reports_rejected += 1
            reporter.trust_score = max(0.0, reporter.trust_score - TRUST_STEP_DOWN)

    append_event(
        db,
        complaint.id,
        EventType.RESOLVED,
        "Complaint resolved and citizen notified.",
        {
            "field_outcome": field_outcome,
            "hours_taken": result.output.get("hours_taken"),
            "reporter_trust": reporter.trust_score if reporter else None,
        },
        actor=AGENT_NAME,
    )
    db.flush()
    return result
