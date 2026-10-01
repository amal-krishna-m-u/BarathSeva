"""Admin command center endpoints — the operational view for officials."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload

from app.agents import resolution as resolution_agent
from app.agents.sla_monitor import sweep
from app.ai.factory import get_provider
from app.api import serializers
from app.api.deps import require_admin
from app.config import settings
from app.core import geo
from app.core.enums import (
    ComplaintCategory,
    ComplaintStatus,
    EventType,
    Priority,
)
from app.core.events import append_event
from app.db import SessionLocal, get_db
from app.models import Complaint, ComplaintEvent, Department, SocialPost, Ward
from app.schemas import (
    ComplaintDetail,
    ComplaintListItem,
    HotspotOut,
    ResolveRequest,
    ReviewRequest,
    SocialPostOut,
    StatsResponse,
    SweepResponse,
    WardOut,
)
from app.workflow.graph import run_intake

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _base_query(db: Session):
    return db.query(Complaint).options(
        joinedload(Complaint.ward),
        joinedload(Complaint.department),
        joinedload(Complaint.evidence),
        joinedload(Complaint.reporter),
    )


@router.get("/complaints", response_model=list[ComplaintListItem])
def list_complaints(
    status: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    ward_id: Optional[int] = None,
    department_code: Optional[str] = None,
    breached: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    hotspot: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[ComplaintListItem]:
    """Filtered complaint feed: by ward, department, category, priority and
    SLA state."""
    query = _base_query(db)

    if status:
        query = query.filter(Complaint.status == ComplaintStatus(status))
    if category:
        query = query.filter(Complaint.category == ComplaintCategory(category))
    if priority:
        query = query.filter(Complaint.priority == Priority(priority))
    if ward_id is not None:
        query = query.filter(Complaint.ward_id == ward_id)
    if department_code:
        dept = db.query(Department).filter_by(code=department_code.upper()).one_or_none()
        query = query.filter(Complaint.department_id == (dept.id if dept else -1))
    if breached is not None:
        query = query.filter(Complaint.sla_breached.is_(breached))
    if needs_review is not None:
        query = query.filter(Complaint.needs_human_review.is_(needs_review))
    if hotspot is not None:
        query = query.filter(Complaint.is_hotspot.is_(hotspot))
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            Complaint.description.ilike(like) | Complaint.reference.ilike(like)
        )

    rows = (
        query.order_by(Complaint.created_at.desc()).limit(limit).offset(offset).all()
    )
    return [serializers.list_item(c) for c in rows]


@router.get("/complaints/{reference}", response_model=ComplaintDetail)
def get_complaint(reference: str, db: Session = Depends(get_db)) -> ComplaintDetail:
    """Full complaint record: evidence signals, audit trail and every agent run."""
    complaint = (
        _base_query(db).filter(Complaint.reference == reference.upper()).one_or_none()
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail=f"No complaint {reference!r}.")
    return serializers.detail(db, complaint)


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    """Counts for the command center header."""

    def grouped(column) -> dict[str, int]:
        rows = db.execute(
            select(column, func.count()).group_by(column)
        ).all()
        out: dict[str, int] = {}
        for value, count in rows:
            if value is None:
                continue
            out[value.value if hasattr(value, "value") else str(value)] = int(count)
        return out

    total = int(db.execute(select(func.count(Complaint.id))).scalar_one())
    by_status = grouped(Complaint.status)
    provider = get_provider()

    def count_where(*conditions) -> int:
        return int(
            db.execute(select(func.count(Complaint.id)).where(*conditions)).scalar_one()
        )

    dept_rows = db.execute(
        select(Department.code, func.count(Complaint.id))
        .join(Complaint, Complaint.department_id == Department.id)
        .group_by(Department.code)
    ).all()

    resolved = by_status.get(ComplaintStatus.RESOLVED.value, 0)
    rejected = by_status.get(ComplaintStatus.REJECTED.value, 0)

    return StatsResponse(
        total=total,
        by_status=by_status,
        by_category=grouped(Complaint.category),
        by_priority=grouped(Complaint.priority),
        by_department={code: int(count) for code, count in dept_rows},
        open_count=total - resolved - rejected,
        resolved_count=resolved,
        rejected_count=rejected,
        breached_count=count_where(Complaint.sla_breached.is_(True)),
        pending_review_count=count_where(Complaint.needs_human_review.is_(True)),
        hotspot_count=count_where(Complaint.is_hotspot.is_(True)),
        ai_provider=provider.name,
        ai_is_real_model=provider.is_ai,
    )


@router.get("/hotspots", response_model=list[HotspotOut])
def get_hotspots(
    radius_meters: Optional[float] = None,
    min_complaints: Optional[int] = None,
    window_hours: int = Query(720, ge=1, le=8760),
    db: Session = Depends(get_db),
) -> list[HotspotOut]:
    """Density-based hotspot clusters via PostGIS ST_ClusterDBSCAN."""
    clusters = geo.hotspots(
        db,
        radius_meters=radius_meters,
        min_complaints=min_complaints,
        window_hours=window_hours,
    )
    return [HotspotOut(**vars(h)) for h in clusters]


@router.get("/wards", response_model=list[WardOut])
def list_wards(
    include_boundary: bool = True, db: Session = Depends(get_db)
) -> list[WardOut]:
    """Ward list with GeoJSON boundaries for the map layer."""
    counts = dict(
        db.execute(
            select(Complaint.ward_id, func.count())
            .where(Complaint.ward_id.isnot(None))
            .group_by(Complaint.ward_id)
        ).all()
    )

    if include_boundary:
        rows = db.execute(
            text(
                "SELECT id, ward_number, name, zone, "
                "ST_AsGeoJSON(boundary) AS geojson FROM wards ORDER BY name"
            )
        ).all()
        return [
            WardOut(
                id=r.id,
                ward_number=r.ward_number,
                name=r.name,
                zone=r.zone,
                complaint_count=int(counts.get(r.id, 0)),
                boundary=json.loads(r.geojson) if r.geojson else {},
            )
            for r in rows
        ]

    wards = db.execute(select(Ward).order_by(Ward.name)).scalars().all()
    return [
        WardOut(
            id=w.id,
            ward_number=w.ward_number,
            name=w.name,
            zone=w.zone,
            complaint_count=int(counts.get(w.id, 0)),
        )
        for w in wards
    ]


@router.get("/social-posts", response_model=list[SocialPostOut])
def list_social_posts(
    limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)
) -> list[SocialPostOut]:
    """Generated accountability content. Held unpublished in the prototype."""
    posts = (
        db.query(SocialPost)
        .options(joinedload(SocialPost.complaint))
        .order_by(SocialPost.id.desc())
        .limit(limit)
        .all()
    )
    return [serializers.social_post_out(p) for p in posts]


@router.post("/sla/sweep", response_model=SweepResponse)
def trigger_sla_sweep(
    limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)
) -> SweepResponse:
    """Run the SLA sweep now.

    The same function the Celery beat schedule calls. Exposed so the lifecycle
    can be demonstrated without a worker process running.
    """
    return SweepResponse(**sweep(db, limit=limit))


@router.post("/complaints/{reference}/resolve", response_model=ComplaintDetail)
def resolve_complaint(
    reference: str, payload: ResolveRequest, db: Session = Depends(get_db)
) -> ComplaintDetail:
    """Close a complaint and generate the citizen's resolution message."""
    complaint = (
        _base_query(db).filter(Complaint.reference == reference.upper()).one_or_none()
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail=f"No complaint {reference!r}.")
    if complaint.status == ComplaintStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Complaint is already resolved.")
    if complaint.status == ComplaintStatus.REJECTED:
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
    db.commit()
    db.refresh(complaint)
    return serializers.detail(db, complaint)


@router.post("/complaints/{reference}/review", response_model=ComplaintDetail)
def review_complaint(
    reference: str, payload: ReviewRequest, db: Session = Depends(get_db)
) -> ComplaintDetail:
    """Human review decision for a held complaint — the manual fallback path.

    Approving re-enters the pipeline from the verifier onward, so an officer's
    judgment replaces the low-confidence automated verdict rather than
    bypassing the rest of the workflow.
    """
    complaint = (
        _base_query(db).filter(Complaint.reference == reference.upper()).one_or_none()
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail=f"No complaint {reference!r}.")
    if not complaint.needs_human_review:
        raise HTTPException(
            status_code=409, detail="Complaint is not awaiting human review."
        )

    if payload.override_category:
        complaint.category = ComplaintCategory(payload.override_category)
    if payload.override_priority:
        complaint.priority = Priority(payload.override_priority)

    complaint.needs_human_review = False
    append_event(
        db,
        complaint.id,
        EventType.REVIEW_DECISION,
        f"Officer {'approved' if payload.approve else 'rejected'} the held complaint."
        + (f" Note: {payload.note}" if payload.note else ""),
        {
            "approve": payload.approve,
            "override_category": payload.override_category,
            "override_priority": payload.override_priority,
        },
        actor="officer",
    )

    if payload.approve:
        complaint.is_verified = True
        complaint.status = ComplaintStatus.VERIFIED
        db.commit()
        run_intake(complaint.id, channel=complaint.channel)
    else:
        complaint.is_verified = False
        complaint.status = ComplaintStatus.REJECTED
        complaint.rejection_reason = payload.note or "Rejected on manual review."
        db.commit()

    db.expire_all()
    complaint = (
        _base_query(db).filter(Complaint.reference == reference.upper()).one()
    )
    return serializers.detail(db, complaint)


@router.get("/stream")
async def stream_events(request: Request, after_id: int = 0) -> StreamingResponse:
    """Server-sent events feed of new audit entries.

    This is what makes the command center update live in the local stack. A
    Supabase deployment would use Supabase Realtime for the same purpose; the
    data source — ``complaint_events`` — is identical either way.
    """

    async def generator():
        cursor = after_id
        if cursor == 0:
            with SessionLocal() as db:
                cursor = int(
                    db.execute(select(func.coalesce(func.max(ComplaintEvent.id), 0)))
                    .scalar_one()
                )
        yield f": connected at event {cursor}\n\n"

        while True:
            if await request.is_disconnected():
                break
            payload: list[dict[str, Any]] = []
            with SessionLocal() as db:
                rows = (
                    db.query(ComplaintEvent)
                    .options(joinedload(ComplaintEvent.complaint))
                    .filter(ComplaintEvent.id > cursor)
                    .order_by(ComplaintEvent.id.asc())
                    .limit(50)
                    .all()
                )
                for event in rows:
                    cursor = max(cursor, event.id)
                    payload.append(
                        {
                            "id": event.id,
                            "reference": event.complaint.reference,
                            "event_type": event.event_type.value,
                            "actor": event.actor,
                            "message": event.message,
                            "status": event.complaint.status.value,
                            "created_at": event.created_at.isoformat(),
                        }
                    )
            for item in payload:
                yield f"data: {json.dumps(item)}\n\n"
            if not payload:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
