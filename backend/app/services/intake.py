"""Complaint intake — the trust boundary.

Order matters here and is deliberate:

  1. A complaint row is created for *every* submission, including ones that
     hard-fail the evidence checks. A rejected report that leaves no record is
     unauditable, and a citizen who was rejected deserves a reference they can
     quote.
  2. Evidence forensics run before the workflow, because their verdict decides
     whether the workflow should run at all.
  3. The capture token is consumed exactly once, so replaying it is detectable.
  4. Everything is committed before the graph is invoked — graph nodes open
     their own sessions and must see a persisted complaint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from app.core import media
from app.core.authenticity import EvidenceAssessment, assess_evidence
from app.core.enums import AuthenticityOutcome, ComplaintStatus, EventType, UserRole
from app.core.events import append_event
from app.core.ids import next_complaint_reference
from app.core.security import consume_capture_token
from app.models import Complaint, ComplaintEvidence, User
from app.workflow.graph import run_intake

logger = logging.getLogger(__name__)


@dataclass
class IntakeRequest:
    description: str
    latitude: float
    longitude: float
    channel: str = "web"
    declared_source: str = "camera"
    capture_token: Optional[str] = None
    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None
    gps_accuracy_meters: Optional[float] = None
    mock_location: bool = False
    address_text: Optional[str] = None
    reporter_phone: Optional[str] = None
    reporter_name: Optional[str] = None
    telegram_chat_id: Optional[str] = None


@dataclass
class IntakeResult:
    complaint_id: int
    reference: str
    accepted: bool
    status: str
    authenticity_score: float
    authenticity_outcome: str
    evidence_signals: list[dict[str, Any]] = field(default_factory=list)
    hard_fail_reason: Optional[str] = None
    workflow_state: Optional[dict[str, Any]] = None


def resolve_reporter(db: Session, request: IntakeRequest) -> Optional[User]:
    """Find or create the reporting citizen. Anonymous reports are allowed."""
    if request.telegram_chat_id:
        user = (
            db.query(User)
            .filter_by(telegram_chat_id=request.telegram_chat_id)
            .one_or_none()
        )
        if user:
            return user
        user = User(
            display_name=request.reporter_name or "Telegram Citizen",
            telegram_chat_id=request.telegram_chat_id,
            role=UserRole.CITIZEN,
            is_verified=True,  # Telegram established a persistent identity
            trust_score=0.5,
        )
        db.add(user)
        db.flush()
        return user

    if request.reporter_phone:
        user = db.query(User).filter_by(phone=request.reporter_phone).one_or_none()
        if user:
            return user
        user = User(
            display_name=request.reporter_name or "Citizen",
            phone=request.reporter_phone,
            role=UserRole.CITIZEN,
            is_verified=False,
            trust_score=0.5,
        )
        db.add(user)
        db.flush()
        return user

    return None


def submit_complaint(db: Session, request: IntakeRequest) -> IntakeResult:
    """Persist a complaint with its evidence assessment, then run the pipeline."""
    reporter = resolve_reporter(db, request)

    assessment: EvidenceAssessment = assess_evidence(
        db,
        latitude=request.latitude,
        longitude=request.longitude,
        channel=request.channel,
        declared_source=request.declared_source,
        capture_token=request.capture_token,
        image_bytes=request.image_bytes,
        image_mime=request.image_mime,
        gps_accuracy_meters=request.gps_accuracy_meters,
        mock_location=request.mock_location,
        reporter=reporter,
    )

    media_path: Optional[str] = None
    if request.image_bytes and assessment.sha256:
        media_path = media.store_image(
            request.image_bytes, assessment.sha256, request.image_mime
        )

    reference = next_complaint_reference(db)
    hard_failed = assessment.outcome == AuthenticityOutcome.HARD_FAIL

    complaint = Complaint(
        reference=reference,
        reporter_id=reporter.id if reporter else None,
        channel=request.channel,
        description=request.description.strip(),
        status=ComplaintStatus.SUBMITTED,
        location=WKTElement(
            f"POINT({request.longitude} {request.latitude})", srid=4326
        ),
        latitude=request.latitude,
        longitude=request.longitude,
        address_text=request.address_text,
    )
    db.add(complaint)
    db.flush()

    evidence = ComplaintEvidence(
        complaint_id=complaint.id,
        source=assessment.source,
        capture_token_id=assessment.capture_token_id,
        capture_token_issued_at=assessment.capture_token_issued_at,
        capture_token_valid=assessment.capture_token_valid,
        server_received_at=assessment.server_received_at,
        media_path=media_path,
        media_mime=request.image_mime,
        media_bytes=assessment.media_bytes,
        image_width=assessment.image_width,
        image_height=assessment.image_height,
        sha256=assessment.sha256,
        phash=assessment.phash,
        exif=assessment.exif or {},
        exif_present=assessment.exif_present,
        exif_datetime=assessment.exif_datetime,
        exif_time_delta_seconds=assessment.exif_time_delta_seconds,
        exif_gps_distance_meters=assessment.exif_gps_distance_meters,
        editing_software=assessment.editing_software,
        gps_accuracy_meters=assessment.gps_accuracy_meters,
        mock_location_flag=assessment.mock_location_flag,
        inside_serviced_ward=assessment.inside_serviced_ward,
        reporter_speed_kmh=assessment.reporter_speed_kmh,
        duplicate_of_complaint_id=assessment.duplicate_of_complaint_id,
        duplicate_distance=assessment.duplicate_distance,
        exact_duplicate=assessment.exact_duplicate,
        signals=assessment.signals_as_json(),
        authenticity_score=assessment.score,
        outcome=assessment.outcome,
        hard_fail_reason=assessment.hard_fail_reason,
    )
    db.add(evidence)
    db.flush()

    append_event(
        db,
        complaint.id,
        EventType.CREATED,
        f"Complaint {reference} received via {request.channel}.",
        {
            "channel": request.channel,
            "has_photo": bool(request.image_bytes),
            "reporter_id": reporter.id if reporter else None,
        },
        actor="intake",
    )
    append_event(
        db,
        complaint.id,
        EventType.EVIDENCE_CHECKED,
        f"Evidence assessed: score {assessment.score:.2f} "
        f"({assessment.outcome.value}).",
        {
            "authenticity_score": assessment.score,
            "outcome": assessment.outcome.value,
            "signal_codes": [s.code for s in assessment.signals],
            "server_received_at": assessment.server_received_at.isoformat(),
            "capture_token_valid": assessment.capture_token_valid,
        },
        actor="evidence_checker",
    )

    if assessment.capture_token_valid and assessment.capture_token_id:
        consume_capture_token(db, assessment.capture_token_id)

    if hard_failed:
        complaint.status = ComplaintStatus.REJECTED
        complaint.rejection_reason = assessment.hard_fail_reason
        append_event(
            db,
            complaint.id,
            EventType.REJECTED,
            assessment.hard_fail_reason or "Evidence hard-failed.",
            {"stage": "evidence", "score": assessment.score},
            actor="evidence_checker",
        )

    db.commit()

    workflow_state: Optional[dict[str, Any]] = None
    if not hard_failed:
        # Graph nodes manage their own sessions; the complaint must be visible.
        workflow_state = dict(run_intake(complaint.id, channel=request.channel))
        db.expire_all()
        db.refresh(complaint)

    return IntakeResult(
        complaint_id=complaint.id,
        reference=reference,
        accepted=not hard_failed,
        status=complaint.status.value,
        authenticity_score=assessment.score,
        authenticity_outcome=assessment.outcome.value,
        evidence_signals=assessment.signals_as_json(),
        hard_fail_reason=assessment.hard_fail_reason,
        workflow_state=workflow_state,
    )
