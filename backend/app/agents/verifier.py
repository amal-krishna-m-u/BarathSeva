"""Verifier agent — the first judgment gate.

Reads the text and image together, alongside the deterministic signal set from
the evidence stage, and decides whether the report describes a genuine civic
issue backed by sufficient evidence to act on.

It does not re-run forensics. Authenticity is Layers 1-4's job; this agent's
job is image-text consistency, and a confidence value that determines whether
the complaint proceeds automatically or waits for a human.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.factory import infer
from app.ai.prompts import build_verify
from app.config import settings
from app.core import media
from app.core.enums import ComplaintStatus, EventType
from app.core.events import append_event
from app.models import Complaint, ComplaintEvidence

AGENT_NAME = "verifier"

#: Below this, even a "valid" verdict is routed to a human instead of dispatch.
MIN_AUTO_CONFIDENCE = 0.45


def _load_image(evidence: Optional[ComplaintEvidence]) -> tuple[Optional[bytes], Optional[str]]:
    if evidence is None or not evidence.media_path:
        return None, None
    path: Path = media.resolve_path(evidence.media_path)
    if not path.exists():
        return None, evidence.media_mime
    return path.read_bytes(), evidence.media_mime


def run(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    evidence = complaint.evidence[0] if complaint.evidence else None
    image_bytes, image_mime = _load_image(evidence)
    has_photo = image_bytes is not None
    signals = list(evidence.signals or []) if evidence else []
    score = float(evidence.authenticity_score) if evidence else 0.0
    outcome = evidence.outcome.value if evidence else "UNKNOWN"

    def _work() -> AgentResult:
        request = build_verify(
            description=complaint.description,
            has_photo=has_photo,
            image_readable=has_photo,
            authenticity_score=score,
            authenticity_outcome=outcome,
            signals=signals,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
        result = infer(request)

        is_civic = bool(result.data.get("is_civic_issue", False))
        sufficient = bool(result.data.get("evidence_sufficient", False))
        confidence = float(result.confidence or 0.0)
        rationale = result.rationale or "No rationale returned."

        valid = is_civic and sufficient
        # Bias: a doubtful report goes to a human, it is not rejected.
        low_confidence = confidence < MIN_AUTO_CONFIDENCE

        return AgentResult(
            output={
                "is_civic_issue": is_civic,
                "evidence_sufficient": sufficient,
                "category_hint": result.data.get("category_hint"),
                "valid": valid,
                "low_confidence": low_confidence,
                "verdict": "valid" if valid else "invalid",
            },
            rationale=rationale,
            confidence=confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            error=result.error,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    valid = bool(result.output.get("valid"))
    low_confidence = bool(result.output.get("low_confidence"))
    complaint.verification_confidence = result.confidence
    complaint.verification_reason = result.rationale

    if not valid:
        complaint.is_verified = False
        complaint.status = ComplaintStatus.REJECTED
        complaint.rejection_reason = result.rationale
        append_event(
            db,
            complaint.id,
            EventType.REJECTED,
            "Report did not describe a verifiable civic issue.",
            {
                "confidence": result.confidence,
                "is_civic_issue": result.output.get("is_civic_issue"),
                "evidence_sufficient": result.output.get("evidence_sufficient"),
            },
            actor=AGENT_NAME,
        )
    elif low_confidence:
        complaint.is_verified = False
        complaint.needs_human_review = True
        complaint.status = ComplaintStatus.PENDING_REVIEW
        append_event(
            db,
            complaint.id,
            EventType.HELD_FOR_REVIEW,
            f"Verified as a civic issue but confidence {result.confidence:.2f} is "
            f"below the {MIN_AUTO_CONFIDENCE:.2f} auto-dispatch floor.",
            {"confidence": result.confidence},
            actor=AGENT_NAME,
        )
    else:
        complaint.is_verified = True
        complaint.status = ComplaintStatus.VERIFIED
        append_event(
            db,
            complaint.id,
            EventType.VERIFIED,
            "Report verified as a genuine civic issue.",
            {
                "confidence": result.confidence,
                "category_hint": result.output.get("category_hint"),
                "provider": result.provider,
            },
            actor=AGENT_NAME,
        )

    db.flush()
    return result
