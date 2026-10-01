"""Department portal — each agency sees only the complaints routed to it.

Scoping is enforced server-side on every query. A DEPT_ADMIN cannot widen it
with a query parameter, and cannot read or act on another department's
complaint even with its reference number: the filter is applied before the
lookup, so a cross-department reference simply returns 404.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.agents import resolution as resolution_agent
from app.api import serializers
from app.core.auth import require_department_admin
from app.core.enums import (
    ComplaintCategory,
    ComplaintStatus,
    EventType,
    Priority,
    UserRole,
)
from app.core.events import append_event
from app.db import get_db
from app.models import Complaint, Department, User, Ward, utcnow
from app.schemas import (
    AcknowledgeRequest,
    ComplaintDetail,
    ComplaintListItem,
    DepartmentStats,
    DepartmentSummary,
    ReassignRequest,
    ResolveRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/department", tags=["department"])

#: Complaints whose deadline falls inside this window are "due soon".
DUE_SOON = timedelta(hours=12)


def _scope(db: Session, staff: User) -> Optional[int]:
    """The department id this caller is limited to, or None for a super admin."""
    return None if staff.role is UserRole.SUPER_ADMIN else staff.department_id


def _base_query(db: Session, staff: User):
    query = db.query(Complaint).options(
        joinedload(Complaint.ward),
        joinedload(Complaint.department),
        joinedload(Complaint.evidence),
        joinedload(Complaint.reporter),
    )
    scope = _scope(db, staff)
    if scope is not None:
        query = query.filter(Complaint.department_id == scope)
    # A complaint only reaches a department once it has been routed there.
    return query.filter(Complaint.department_id.isnot(None))


def _get_scoped(db: Session, staff: User, reference: str) -> Complaint:
    complaint = (
        _base_query(db, staff)
        .filter(Complaint.reference == reference.upper())
        .one_or_none()
    )
    if complaint is None:
        # Deliberately indistinguishable from "does not exist": a department
        # should not be able to probe for other agencies' reference numbers.
        raise HTTPException(
            status_code=404, detail=f"No complaint {reference!r} assigned to you."
        )
    return complaint


@router.get("/me", response_model=DepartmentSummary)
def my_department(
    staff: User = Depends(require_department_admin), db: Session = Depends(get_db)
) -> DepartmentSummary:
    department = staff.department or (
        db.query(Department).order_by(Department.id).first()
    )
    if department is None:
        raise HTTPException(status_code=404, detail="No department configured.")
    return serializers.department_summary(department)


@router.get("/complaints", response_model=list[ComplaintListItem])
def list_department_complaints(
    status: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    ward_id: Optional[int] = None,
    breached: Optional[bool] = None,
    unacknowledged: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    staff: User = Depends(require_department_admin),
    db: Session = Depends(get_db),
) -> list[ComplaintListItem]:
    """The department's inbox: complaints routed to this agency only."""
    query = _base_query(db, staff)

    if status:
        query = query.filter(Complaint.status == ComplaintStatus(status))
    if category:
        query = query.filter(Complaint.category == ComplaintCategory(category))
    if priority:
        query = query.filter(Complaint.priority == Priority(priority))
    if ward_id is not None:
        query = query.filter(Complaint.ward_id == ward_id)
    if breached is not None:
        query = query.filter(Complaint.sla_breached.is_(breached))
    if unacknowledged is True:
        query = query.filter(Complaint.acknowledged_at.is_(None))
    elif unacknowledged is False:
        query = query.filter(Complaint.acknowledged_at.isnot(None))
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            Complaint.description.ilike(like)
            | Complaint.reference.ilike(like)
            | Complaint.external_ticket_id.ilike(like)
        )

    rows = (
        query.order_by(
            Complaint.sla_breached.desc(),
            Complaint.sla_due_at.asc().nullslast(),
            Complaint.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [serializers.list_item(c) for c in rows]


@router.get("/complaints/{reference}", response_model=ComplaintDetail)
def get_department_complaint(
    reference: str,
    staff: User = Depends(require_department_admin),
    db: Session = Depends(get_db),
) -> ComplaintDetail:
    return serializers.detail(db, _get_scoped(db, staff, reference))


@router.get("/stats", response_model=DepartmentStats)
def department_stats(
    staff: User = Depends(require_department_admin), db: Session = Depends(get_db)
) -> DepartmentStats:
    """Counts for this department's dashboard header."""
    scope = _scope(db, staff)
    department = staff.department or db.query(Department).order_by(Department.id).first()
    if department is None:
        raise HTTPException(status_code=404, detail="No department configured.")

    def count(*conditions) -> int:
        statement = select(func.count(Complaint.id)).where(
            Complaint.department_id.isnot(None), *conditions
        )
        if scope is not None:
            statement = statement.where(Complaint.department_id == scope)
        return int(db.execute(statement).scalar_one())

    def grouped(column) -> dict[str, int]:
        statement = (
            select(column, func.count())
            .where(Complaint.department_id.isnot(None))
            .group_by(column)
        )
        if scope is not None:
            statement = statement.where(Complaint.department_id == scope)
        out: dict[str, int] = {}
        for value, total in db.execute(statement).all():
            if value is None:
                continue
            out[value.value if hasattr(value, "value") else str(value)] = int(total)
        return out

    ward_statement = (
        select(Complaint.ward_id, func.count())
        .where(Complaint.department_id.isnot(None), Complaint.ward_id.isnot(None))
        .group_by(Complaint.ward_id)
    )
    if scope is not None:
        ward_statement = ward_statement.where(Complaint.department_id == scope)
    ward_rows = db.execute(ward_statement).all()

    ward_names: dict[str, int] = {}
    if ward_rows:
        lookup = {
            ward.id: ward.name
            for ward in db.execute(
                select(Ward).where(Ward.id.in_([row[0] for row in ward_rows]))
            )
            .scalars()
            .all()
        }
        for ward_id, total in ward_rows:
            ward_names[lookup.get(ward_id, f"Ward {ward_id}")] = int(total)

    open_statuses = [ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED]
    return DepartmentStats(
        department=serializers.department_summary(department),
        total=count(),
        unacknowledged=count(
            Complaint.acknowledged_at.is_(None),
            Complaint.status.notin_(open_statuses),
        ),
        in_progress=count(Complaint.status == ComplaintStatus.IN_PROGRESS),
        resolved=count(Complaint.status == ComplaintStatus.RESOLVED),
        breached=count(Complaint.sla_breached.is_(True)),
        due_soon=count(
            Complaint.sla_due_at.isnot(None),
            Complaint.sla_due_at <= utcnow() + DUE_SOON,
            Complaint.sla_breached.is_(False),
            Complaint.status.notin_(open_statuses),
        ),
        by_priority=grouped(Complaint.priority),
        by_category=grouped(Complaint.category),
        by_ward=ward_names,
    )


@router.post("/complaints/{reference}/acknowledge", response_model=ComplaintDetail)
def acknowledge(
    reference: str,
    payload: AcknowledgeRequest,
    staff: User = Depends(require_department_admin),
    db: Session = Depends(get_db),
) -> ComplaintDetail:
    """Department accepts the ticket and starts work."""
    complaint = _get_scoped(db, staff, reference)
    if complaint.status in {ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED}:
        raise HTTPException(status_code=409, detail="Complaint is already closed.")
    if complaint.acknowledged_at is not None:
        raise HTTPException(status_code=409, detail="Already acknowledged.")

    complaint.acknowledged_at = utcnow()
    complaint.acknowledged_by_id = staff.id
    complaint.status = ComplaintStatus.IN_PROGRESS
    append_event(
        db,
        complaint.id,
        EventType.STATUS_CHANGED,
        f"{complaint.department.code} acknowledged the ticket and began work."
        + (f" Note: {payload.note}" if payload.note else ""),
        {
            "department_code": complaint.department.code,
            "acknowledged_by": staff.display_name,
            "note": payload.note,
        },
        actor=f"dept:{complaint.department.code}",
    )
    db.commit()
    db.refresh(complaint)
    return serializers.detail(db, complaint)


@router.post("/complaints/{reference}/resolve", response_model=ComplaintDetail)
def resolve(
    reference: str,
    payload: ResolveRequest,
    staff: User = Depends(require_department_admin),
    db: Session = Depends(get_db),
) -> ComplaintDetail:
    """Close the complaint and generate the citizen's resolution message."""
    complaint = _get_scoped(db, staff, reference)
    if complaint.status is ComplaintStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Complaint is already resolved.")
    if complaint.status is ComplaintStatus.REJECTED:
        raise HTTPException(
            status_code=409, detail="A rejected complaint cannot be resolved."
        )

    resolution_agent.run(
        db,
        complaint,
        {"complaint_id": complaint.id, "reference": complaint.reference},
        resolution_note=payload.resolution_note,
        field_outcome=payload.field_outcome,
    )
    append_event(
        db,
        complaint.id,
        EventType.STATUS_CHANGED,
        f"Closed by {staff.display_name} at {complaint.department.code}.",
        {"field_outcome": payload.field_outcome, "closed_by": staff.display_name},
        actor=f"dept:{complaint.department.code}",
    )
    db.commit()
    db.refresh(complaint)
    return serializers.detail(db, complaint)


@router.post("/complaints/{reference}/reassign", response_model=ComplaintDetail)
def reassign(
    reference: str,
    payload: ReassignRequest,
    staff: User = Depends(require_department_admin),
    db: Session = Depends(get_db),
) -> ComplaintDetail:
    """Hand a misrouted complaint to the department that actually owns it.

    Routing is deterministic, but a rule table can still be wrong about a
    specific case. Rather than letting a department close what is not theirs,
    this moves ownership and records who moved it and why.
    """
    complaint = _get_scoped(db, staff, reference)
    if complaint.status in {ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED}:
        raise HTTPException(status_code=409, detail="Complaint is already closed.")

    target = (
        db.query(Department)
        .filter(Department.code == payload.target_department_code.upper())
        .one_or_none()
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"No department {payload.target_department_code!r}.",
        )
    if target.id == complaint.department_id:
        raise HTTPException(
            status_code=409, detail="Complaint is already with that department."
        )

    previous = complaint.department.code
    complaint.department_id = target.id
    complaint.acknowledged_at = None
    complaint.acknowledged_by_id = None
    complaint.status = ComplaintStatus.DISPATCHED
    append_event(
        db,
        complaint.id,
        EventType.DISPATCHED,
        f"Reassigned from {previous} to {target.code}: {payload.reason}",
        {
            "from_department": previous,
            "to_department": target.code,
            "reason": payload.reason,
            "reassigned_by": staff.display_name,
        },
        actor=f"dept:{previous}",
    )
    db.commit()
    db.refresh(complaint)
    logger.info("complaint %s reassigned %s -> %s", reference, previous, target.code)
    return serializers.detail(db, complaint)
