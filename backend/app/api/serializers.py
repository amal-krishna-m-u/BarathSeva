"""Model -> schema conversion, kept in one place so every endpoint agrees."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.events import verify_chain
from app.models import (
    AgentRun,
    Complaint,
    ComplaintEvent,
    ComplaintEvidence,
    Department,
    SocialPost,
    User,
)
from app.schemas import (
    AgentRunOut,
    DepartmentSummary,
    UserOut,
    ComplaintDetail,
    ComplaintListItem,
    ComplaintStatusResponse,
    EventOut,
    EvidenceOut,
    SignalOut,
    SocialPostOut,
)


def _enum(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def photo_url(evidence: Optional[ComplaintEvidence]) -> Optional[str]:
    if evidence is None or not evidence.media_path:
        return None
    return f"/api/media/{evidence.media_path}"


def evidence_out(evidence: Optional[ComplaintEvidence]) -> Optional[EvidenceOut]:
    if evidence is None:
        return None
    return EvidenceOut(
        source=_enum(evidence.source) or "none",
        capture_token_valid=evidence.capture_token_valid,
        server_received_at=evidence.server_received_at,
        authenticity_score=evidence.authenticity_score,
        outcome=_enum(evidence.outcome) or "UNKNOWN",
        hard_fail_reason=evidence.hard_fail_reason,
        signals=[SignalOut(**s) for s in (evidence.signals or [])],
        sha256=evidence.sha256,
        phash=evidence.phash,
        media_path=evidence.media_path,
        image_width=evidence.image_width,
        image_height=evidence.image_height,
        exif_present=evidence.exif_present,
        exif_datetime=evidence.exif_datetime,
        exif_time_delta_seconds=evidence.exif_time_delta_seconds,
        exif_gps_distance_meters=evidence.exif_gps_distance_meters,
        editing_software=evidence.editing_software,
        gps_accuracy_meters=evidence.gps_accuracy_meters,
        location_source=_enum(evidence.location_source) or "unknown",
        mock_location_flag=evidence.mock_location_flag,
        inside_serviced_ward=evidence.inside_serviced_ward,
        duplicate_of_complaint_id=evidence.duplicate_of_complaint_id,
        exact_duplicate=evidence.exact_duplicate,
    )


def event_out(event: ComplaintEvent) -> EventOut:
    return EventOut(
        id=event.id,
        event_type=_enum(event.event_type) or "",
        actor=event.actor,
        message=event.message,
        payload=event.payload or {},
        created_at=event.created_at,
        entry_hash=event.entry_hash,
    )


def agent_run_out(run: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        id=run.id,
        agent_name=run.agent_name,
        status=_enum(run.status) or "",
        is_ai=run.is_ai,
        provider=run.provider,
        model=run.model,
        confidence=run.confidence,
        latency_ms=run.latency_ms,
        rationale=run.rationale,
        output=run.output or {},
        error=run.error,
        created_at=run.created_at,
    )


def list_item(complaint: Complaint) -> ComplaintListItem:
    evidence = complaint.evidence[0] if complaint.evidence else None
    return ComplaintListItem(
        id=complaint.id,
        reference=complaint.reference,
        description=complaint.description,
        status=_enum(complaint.status) or "",
        category=_enum(complaint.category),
        priority=_enum(complaint.priority),
        ward_name=complaint.ward.name if complaint.ward else None,
        department_code=complaint.department.code if complaint.department else None,
        external_ticket_id=complaint.external_ticket_id,
        latitude=complaint.latitude,
        longitude=complaint.longitude,
        nearby_count=complaint.nearby_count,
        is_hotspot=complaint.is_hotspot,
        sla_due_at=complaint.sla_due_at,
        sla_breached=complaint.sla_breached,
        escalation_level=complaint.escalation_level,
        needs_human_review=complaint.needs_human_review,
        authenticity_score=evidence.authenticity_score if evidence else None,
        authenticity_outcome=_enum(evidence.outcome) if evidence else None,
        photo_url=photo_url(evidence),
        created_at=complaint.created_at,
    )


def detail(db: Session, complaint: Complaint) -> ComplaintDetail:
    evidence = complaint.evidence[0] if complaint.evidence else None
    intact, _ = verify_chain(db, complaint.id)
    base = list_item(complaint).model_dump()
    return ComplaintDetail(
        **base,
        address_text=complaint.address_text,
        severity_note=complaint.severity_note,
        verification_reason=complaint.verification_reason,
        rejection_reason=complaint.rejection_reason,
        cluster_summary=complaint.cluster_summary,
        resolution_note=complaint.resolution_note,
        resolution_message=complaint.resolution_message,
        resolved_at=complaint.resolved_at,
        field_outcome=complaint.field_outcome,
        reporter_name=complaint.reporter.display_name if complaint.reporter else None,
        reporter_trust=complaint.reporter.trust_score if complaint.reporter else None,
        evidence=evidence_out(evidence),
        events=[event_out(e) for e in complaint.events],
        agent_runs=[agent_run_out(r) for r in complaint.agent_runs],
        audit_chain_intact=intact,
    )


def citizen_status(complaint: Complaint) -> ComplaintStatusResponse:
    evidence = complaint.evidence[0] if complaint.evidence else None
    return ComplaintStatusResponse(
        reference=complaint.reference,
        received=True,
        received_at=complaint.created_at,
        verified=complaint.is_verified,
        verification_reason=complaint.verification_reason,
        status=_enum(complaint.status) or "",
        category=_enum(complaint.category),
        priority=_enum(complaint.priority),
        ward_name=complaint.ward.name if complaint.ward else None,
        department_code=complaint.department.code if complaint.department else None,
        department_name=complaint.department.full_name if complaint.department else None,
        external_ticket_id=complaint.external_ticket_id,
        sla_due_at=complaint.sla_due_at,
        sla_breached=complaint.sla_breached,
        escalation_level=complaint.escalation_level,
        needs_human_review=complaint.needs_human_review,
        is_hotspot=complaint.is_hotspot,
        nearby_count=complaint.nearby_count,
        cluster_summary=complaint.cluster_summary,
        resolved=_enum(complaint.status) == "RESOLVED",
        resolved_at=complaint.resolved_at,
        resolution_message=complaint.resolution_message,
        rejection_reason=complaint.rejection_reason,
        authenticity_score=evidence.authenticity_score if evidence else None,
        authenticity_outcome=_enum(evidence.outcome) if evidence else None,
        photo_url=photo_url(evidence),
        events=[event_out(e) for e in complaint.events],
    )


def social_post_out(post: SocialPost) -> SocialPostOut:
    return SocialPostOut(
        id=post.id,
        complaint_reference=post.complaint.reference,
        kind=post.kind,
        content=post.content,
        eligibility_reason=post.eligibility_reason,
        is_published=post.is_published,
        created_at=post.created_at,
    )


def department_summary(department: Optional[Department]) -> Optional[DepartmentSummary]:
    if department is None:
        return None
    return DepartmentSummary(
        id=department.id,
        code=department.code,
        name=department.name,
        full_name=department.full_name,
        service_label=department.service_label,
        categories=list(department.categories or []),
        is_mock=department.is_mock,
    )


def user_out(user: User) -> UserOut:
    """Public shape of an account. Never includes password_hash."""
    return UserOut(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        phone=user.phone,
        role=_enum(user.role) or "CITIZEN",
        is_verified=user.is_verified,
        is_active=user.is_active,
        trust_score=user.trust_score,
        reports_confirmed=user.reports_confirmed,
        reports_rejected=user.reports_rejected,
        department=department_summary(user.department),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )
