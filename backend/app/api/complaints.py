"""Citizen-facing endpoints: capture tokens, intake, tracking."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api import serializers
from app.config import settings
from app.core import media
from app.core.security import issue_capture_token
from app.core.auth import get_current_user, get_current_user_optional
from app.core.enums import ComplaintStatus, LocationSource
from app.db import get_db
from app.models import Complaint, User
from sqlalchemy.orm import joinedload
from app.schemas import (
    CaptureTokenRequest,
    CaptureTokenResponse,
    ComplaintListItem,
    ComplaintStatusResponse,
    ComplaintSubmitResponse,
    EventOut,
    SignalOut,
)
from app.services.intake import IntakeRequest, submit_complaint

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["citizen"])

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
ALLOWED_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}


@router.post("/capture-token", response_model=CaptureTokenResponse)
def create_capture_token(
    payload: CaptureTokenRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> CaptureTokenResponse:
    """Issue a short-lived, single-use capture token (evidence Layer 1).

    The client must call this *before* opening the camera. The token is what
    binds the eventual upload to a server-known moment and GPS fix.
    """
    user: Optional[User] = current_user
    if user is None and payload.phone:
        user = db.query(User).filter_by(phone=payload.phone).one_or_none()

    issued = issue_capture_token(
        db,
        user_id=user.id if user else None,
        device_id=payload.device_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.commit()
    return CaptureTokenResponse(
        token=issued.token,
        token_id=issued.token_id,
        issued_at=issued.issued_at,
        expires_at=issued.expires_at,
        ttl_seconds=issued.ttl_seconds,
    )


@router.post("/complaints", response_model=ComplaintSubmitResponse)
async def create_complaint(
    description: str = Form(..., min_length=5, max_length=4000),
    latitude: float = Form(..., ge=-90, le=90),
    longitude: float = Form(..., ge=-180, le=180),
    channel: str = Form("web"),
    declared_source: str = Form("camera"),
    capture_token: Optional[str] = Form(None),
    gps_accuracy_meters: Optional[float] = Form(None),
    mock_location: bool = Form(False),
    location_source: str = Form(LocationSource.UNKNOWN.value),
    address_text: Optional[str] = Form(None),
    reporter_phone: Optional[str] = Form(None),
    reporter_name: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> ComplaintSubmitResponse:
    """Submit a complaint. One message is enough to start the whole workflow.

    Returns the full pipeline result, including the evidence signal set, so the
    citizen can see exactly what was checked and why their report was accepted,
    flagged or rejected.
    """
    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None

    if photo is not None and photo.filename:
        image_mime = (photo.content_type or "").lower() or None
        if image_mime and image_mime not in ALLOWED_MIMES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported image type {image_mime!r}. Allowed: "
                f"{sorted(ALLOWED_MIMES)}",
            )
        image_bytes = await photo.read()
        if len(image_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        if not image_bytes:
            image_bytes = None

    result = submit_complaint(
        db,
        IntakeRequest(
            description=description,
            latitude=latitude,
            longitude=longitude,
            channel=channel,
            declared_source=declared_source,
            capture_token=capture_token,
            image_bytes=image_bytes,
            image_mime=image_mime,
            gps_accuracy_meters=gps_accuracy_meters,
            mock_location=mock_location,
            location_source=location_source,
            address_text=address_text,
            reporter_phone=reporter_phone,
            reporter_name=reporter_name,
            authenticated_user=current_user,
        ),
    )

    complaint = db.get(Complaint, result.complaint_id)
    state = result.workflow_state or {}

    # "accepted" is the citizen-facing question: did this report enter the
    # municipal workflow? A report can clear the evidence checks and still be
    # rejected by the verifier (a greeting with a valid photo), so the final
    # status is what decides — not the evidence stage alone.
    accepted = complaint.status != ComplaintStatus.REJECTED

    if not accepted:
        reason = result.hard_fail_reason or complaint.rejection_reason or ""
        message = (
            "Your report could not be accepted automatically. "
            f"{reason} "
            f"Keep reference {result.reference} — it is on record and you can "
            "resubmit with a fresh in-app photo."
        ).strip()
    elif complaint.needs_human_review:
        message = (
            f"Report {result.reference} received and held for human review "
            "before dispatch. A reviewer will look at it shortly."
        )
    elif complaint.external_ticket_id:
        message = (
            f"Report {result.reference} verified and routed to "
            f"{complaint.department.code if complaint.department else 'the department'} "
            f"as ticket {complaint.external_ticket_id}."
        )
    else:
        message = f"Report {result.reference} received and is being processed."

    return ComplaintSubmitResponse(
        reference=result.reference,
        accepted=accepted,
        status=complaint.status.value,
        message=message,
        authenticity_score=result.authenticity_score,
        authenticity_outcome=result.authenticity_outcome,
        evidence_signals=[SignalOut(**s) for s in result.evidence_signals],
        rejection_reason=complaint.rejection_reason,
        category=complaint.category.value if complaint.category else None,
        priority=complaint.priority.value if complaint.priority else None,
        ward_name=complaint.ward.name if complaint.ward else None,
        nearby_count=complaint.nearby_count,
        is_hotspot=complaint.is_hotspot,
        department_code=complaint.department.code if complaint.department else None,
        external_ticket_id=complaint.external_ticket_id,
        sla_due_at=complaint.sla_due_at,
        needs_human_review=complaint.needs_human_review,
        pipeline_trace=list(state.get("trace", [])),
    )


def _get_by_reference(db: Session, reference: str) -> Complaint:
    complaint = (
        db.query(Complaint).filter(Complaint.reference == reference.upper()).one_or_none()
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail=f"No complaint {reference!r}.")
    return complaint


@router.get("/complaints/{reference}", response_model=ComplaintStatusResponse)
def get_complaint_status(
    reference: str, db: Session = Depends(get_db)
) -> ComplaintStatusResponse:
    """The citizen transparency view — every question from the problem
    statement answered from stored state."""
    return serializers.citizen_status(_get_by_reference(db, reference))


@router.get("/complaints/{reference}/events", response_model=list[EventOut])
def get_complaint_events(reference: str, db: Session = Depends(get_db)) -> list[EventOut]:
    """The public audit trail for one complaint."""
    complaint = _get_by_reference(db, reference)
    return [serializers.event_out(e) for e in complaint.events]


@router.get("/media/{path:path}")
def get_media(path: str):
    """Serve stored evidence images."""
    resolved = media.resolve_path(path).resolve()
    root = media.media_root().resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Media not found.")
    return FileResponse(resolved)


@router.get("/me/complaints", response_model=list[ComplaintListItem])
def my_complaints(
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ComplaintListItem]:
    """Every complaint this citizen has filed.

    Scoped by the session's user id, never by a query parameter — otherwise
    anyone could read anyone else's reports by changing a number.
    """
    rows = (
        db.query(Complaint)
        .options(
            joinedload(Complaint.ward),
            joinedload(Complaint.department),
            joinedload(Complaint.evidence),
        )
        .filter(Complaint.reporter_id == user.id)
        .order_by(Complaint.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [serializers.list_item(c) for c in rows]
