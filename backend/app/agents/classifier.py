"""Classifier agent — category and priority.

The model proposes; deterministic rules constrain. Any value that is not a
member of the controlled vocabulary is coerced, and the coercion is recorded,
so priority stays explainable and consistent with policy rather than varying
with phrasing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.coerce import as_enum
from app.ai.factory import infer
from app.ai.prompts import build_classify
from app.core import media
from app.core.enums import ComplaintCategory, ComplaintStatus, EventType, Priority
from app.core.events import append_event
from app.models import Complaint

AGENT_NAME = "classifier"

VALID_CATEGORIES = {c.value for c in ComplaintCategory}
VALID_PRIORITIES = {p.value for p in Priority}


def _coerce(value: Any, allowed: set[str], default: str) -> tuple[str, bool]:
    """Return (value, was_coerced).

    Normalises separators before deferring to ``as_enum`` for the actual
    membership check, so a model answer like "pot-hole" or "Road Damage"
    still matches the controlled vocabulary instead of being discarded.
    """
    candidate = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    coerced = as_enum(candidate, allowed, default)
    return coerced, coerced != candidate


def run(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    evidence = complaint.evidence[0] if complaint.evidence else None
    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None
    if evidence and evidence.media_path:
        path: Path = media.resolve_path(evidence.media_path)
        if path.exists():
            image_bytes, image_mime = path.read_bytes(), evidence.media_mime

    def _work() -> AgentResult:
        result = infer(
            build_classify(
                description=complaint.description,
                # The cluster is not known yet — GeoCluster runs next and may
                # raise the priority deterministically once it is.
                nearby_count=0,
                ward_name=complaint.ward.name if complaint.ward else None,
                category_hint=state.get("category_hint"),
                image_bytes=image_bytes,
                image_mime=image_mime,
            )
        )

        category, cat_coerced = _coerce(
            result.data.get("category"), VALID_CATEGORIES, ComplaintCategory.OTHER.value
        )
        priority, prio_coerced = _coerce(
            result.data.get("priority"), VALID_PRIORITIES, Priority.P3.value
        )
        note = str(result.data.get("severity_note") or "").strip()

        coercions = []
        if cat_coerced:
            coercions.append(f"category {result.data.get('category')!r} -> {category}")
        if prio_coerced:
            coercions.append(f"priority {result.data.get('priority')!r} -> {priority}")

        rationale = result.rationale or ""
        if coercions:
            rationale += (
                f" Output coerced into the controlled vocabulary: "
                f"{'; '.join(coercions)}."
            )

        return AgentResult(
            output={
                "category": category,
                "priority": priority,
                "severity_note": note,
                "coerced": coercions,
            },
            rationale=rationale.strip(),
            confidence=result.confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            error=result.error,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    category = result.output.get("category") or ComplaintCategory.OTHER.value
    priority = result.output.get("priority") or Priority.P3.value
    complaint.category = ComplaintCategory(category)
    complaint.priority = Priority(priority)
    complaint.severity_note = result.output.get("severity_note")
    complaint.status = ComplaintStatus.CLASSIFIED

    append_event(
        db,
        complaint.id,
        EventType.CLASSIFIED,
        f"Classified as {category} at priority {priority}.",
        {
            "category": category,
            "priority": priority,
            "confidence": result.confidence,
            "coerced": result.output.get("coerced"),
            "provider": result.provider,
        },
        actor=AGENT_NAME,
    )
    db.flush()
    return result
