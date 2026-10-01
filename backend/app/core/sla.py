"""SLA engine — deadlines are arithmetic, not opinion.

Deadlines derive from the ``sla_policies`` table so they are consistent,
explainable, and changeable without touching pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ComplaintCategory, ComplaintStatus, Priority
from app.models import Complaint, SLAPolicy, utcnow


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class SLAComputation:
    policy_id: Optional[int]
    due_at: Optional[datetime]
    escalate_at: Optional[datetime]
    resolution_hours: Optional[int]
    escalation_hours: Optional[int]
    reason: str


def find_policy(
    db: Session,
    category: Optional[ComplaintCategory | str],
    priority: Optional[Priority | str],
) -> Optional[SLAPolicy]:
    if category is None or priority is None:
        return None
    cat = category.value if hasattr(category, "value") else str(category)
    prio = priority.value if hasattr(priority, "value") else str(priority)
    return db.execute(
        select(SLAPolicy).where(
            SLAPolicy.category == cat, SLAPolicy.priority == prio
        )
    ).scalar_one_or_none()


def compute_sla(
    db: Session,
    category: Optional[ComplaintCategory | str],
    priority: Optional[Priority | str],
    start: Optional[datetime] = None,
) -> SLAComputation:
    """Compute the resolution deadline and escalation point from policy."""
    anchor = _aware(start) or utcnow()
    policy = find_policy(db, category, priority)
    if policy is None:
        return SLAComputation(
            policy_id=None,
            due_at=None,
            escalate_at=None,
            resolution_hours=None,
            escalation_hours=None,
            reason=f"No SLA policy for {category}/{priority}; no deadline set.",
        )
    return SLAComputation(
        policy_id=policy.id,
        due_at=anchor + timedelta(hours=policy.resolution_hours),
        escalate_at=anchor + timedelta(hours=policy.escalation_hours),
        resolution_hours=policy.resolution_hours,
        escalation_hours=policy.escalation_hours,
        reason=(
            f"SLA policy {policy.category.value}/{policy.priority.value}: resolve within "
            f"{policy.resolution_hours}h (escalate after "
            f"{policy.escalation_hours}h) from dispatch."
        ),
    )


def apply_sla(db: Session, complaint: Complaint, start: Optional[datetime] = None) -> SLAComputation:
    """Attach the computed deadline to a complaint."""
    computation = compute_sla(db, complaint.category, complaint.priority, start)
    complaint.sla_policy_id = computation.policy_id
    complaint.sla_due_at = computation.due_at
    complaint.sla_escalate_at = computation.escalate_at
    complaint.sla_breached = False
    db.flush()
    return computation


@dataclass
class SLAStatus:
    due_at: Optional[datetime]
    breached: bool
    overdue_seconds: int
    escalation_due: bool
    remaining_seconds: Optional[int]


def evaluate(complaint: Complaint, now: Optional[datetime] = None) -> SLAStatus:
    """Pure evaluation of a complaint's SLA position — no DB writes."""
    moment = _aware(now) or utcnow()
    due = _aware(complaint.sla_due_at)
    escalate = _aware(complaint.sla_escalate_at)

    if due is None:
        return SLAStatus(None, False, 0, False, None)

    delta = (moment - due).total_seconds()
    breached = delta > 0
    escalation_due = bool(
        escalate
        and moment >= escalate
        and ComplaintStatus(complaint.status) not in
        {ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED}
    )
    return SLAStatus(
        due_at=due,
        breached=breached,
        overdue_seconds=int(max(delta, 0)),
        escalation_due=escalation_due,
        remaining_seconds=int(-delta) if delta < 0 else 0,
    )
