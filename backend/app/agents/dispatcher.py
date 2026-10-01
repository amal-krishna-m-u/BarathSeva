"""Dispatcher agent — department resolution and ticket creation.

Entirely deterministic rules plus an integration call. A department mapping
must be correct every time, so no model participates: the category/ward pair is
looked up in the ``departments`` routing table and the ticket is created
against that department's API.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.core.enums import AgentStatus, ComplaintStatus, EventType
from app.core.events import append_event
from app.core.departments import resolve_department
from app.integrations.mock_gov import MockGovernmentGateway, gateway as default_gateway
from app.models import Complaint, utcnow

AGENT_NAME = "dispatcher"


def run(
    db: Session,
    complaint: Complaint,
    state: dict[str, Any],
    gateway: MockGovernmentGateway | None = None,
) -> AgentResult:
    gw = gateway or default_gateway

    def _work() -> AgentResult:
        department, reason = resolve_department(db, complaint.category)
        if department is None:
            return AgentResult(
                output={"dispatched": False, "reason": reason},
                rationale=reason,
                confidence=1.0,
                provider="python",
                model="deterministic",
                is_ai=False,
                status=AgentStatus.FAILED,
                error=reason,
            )

        response = gw.create_ticket(
            department.code,
            reference=complaint.reference,
            category=complaint.category.value if complaint.category else "OTHER",
            priority=complaint.priority.value if complaint.priority else "P3",
            ward_name=complaint.ward.name if complaint.ward else None,
            description=complaint.description,
            latitude=complaint.latitude,
            longitude=complaint.longitude,
        )

        if not response.ok:
            return AgentResult(
                output={
                    "dispatched": False,
                    "department_id": department.id,
                    "department_code": department.code,
                    "reason": reason,
                },
                rationale=f"{reason} Ticket creation failed: {response.error}",
                confidence=1.0,
                provider="python",
                model="deterministic",
                is_ai=False,
                status=AgentStatus.FAILED,
                error=response.error,
            )

        return AgentResult(
            output={
                "dispatched": True,
                "department_id": department.id,
                "department_code": department.code,
                "department_name": department.full_name,
                "external_ticket_id": response.ticket_id,
                "is_mock": department.is_mock,
                "raw_response": response.raw,
                "reason": reason,
            },
            rationale=(
                f"{reason} Ticket {response.ticket_id} created against the "
                f"{'mock ' if department.is_mock else ''}{department.code} endpoint."
            ),
            confidence=1.0,
            provider="python",
            model="deterministic",
            is_ai=False,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    if result.output.get("dispatched"):
        complaint.department_id = result.output.get("department_id")
        complaint.external_ticket_id = result.output.get("external_ticket_id")
        complaint.dispatched_at = utcnow()
        complaint.status = ComplaintStatus.DISPATCHED
        append_event(
            db,
            complaint.id,
            EventType.DISPATCHED,
            f"Routed to {result.output.get('department_code')} as ticket "
            f"{result.output.get('external_ticket_id')}.",
            {
                "department_code": result.output.get("department_code"),
                "external_ticket_id": result.output.get("external_ticket_id"),
                "is_mock": result.output.get("is_mock"),
                "routing_reason": result.output.get("reason"),
            },
            actor=AGENT_NAME,
        )
    else:
        # Dispatch failure must not lose the complaint: hold it for an officer.
        complaint.needs_human_review = True
        complaint.status = ComplaintStatus.PENDING_REVIEW
        if result.output.get("department_id"):
            complaint.department_id = result.output.get("department_id")
        append_event(
            db,
            complaint.id,
            EventType.HELD_FOR_REVIEW,
            f"Dispatch failed and the complaint was held for manual routing: "
            f"{result.error}",
            {"error": result.error, "department_code": result.output.get("department_code")},
            actor=AGENT_NAME,
        )

    db.flush()
    return result
