"""Append-only, hash-chained audit trail.

Each entry links to its predecessor, so a state change cannot be backdated or
quietly rewritten without breaking the chain. This proves the *record* was not
altered after intake; it says nothing about whether the photo is authentic.
Those are different guarantees and the platform needs both.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import EventType
from app.core.hashing import chain_hash
from app.models import ComplaintEvent


def append_event(
    db: Session,
    complaint_id: int,
    event_type: EventType,
    message: str = "",
    payload: Optional[dict[str, Any]] = None,
    actor: str = "system",
) -> ComplaintEvent:
    """Append one tamper-evident audit entry."""
    previous = db.execute(
        select(ComplaintEvent.entry_hash)
        .where(ComplaintEvent.complaint_id == complaint_id)
        .order_by(ComplaintEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    body = {
        "complaint_id": complaint_id,
        "event_type": event_type.value,
        "actor": actor,
        "message": message,
        "payload": payload or {},
    }
    event = ComplaintEvent(
        complaint_id=complaint_id,
        event_type=event_type,
        actor=actor,
        message=message,
        payload=payload or {},
        prev_hash=previous,
        entry_hash=chain_hash(previous, body),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session, complaint_id: int) -> tuple[bool, Optional[int]]:
    """Recompute the chain. Returns (intact, first_broken_event_id)."""
    events = (
        db.execute(
            select(ComplaintEvent)
            .where(ComplaintEvent.complaint_id == complaint_id)
            .order_by(ComplaintEvent.id.asc())
        )
        .scalars()
        .all()
    )
    previous: Optional[str] = None
    for event in events:
        body = {
            "complaint_id": event.complaint_id,
            "event_type": event.event_type.value
            if hasattr(event.event_type, "value")
            else str(event.event_type),
            "actor": event.actor,
            "message": event.message or "",
            "payload": event.payload or {},
        }
        expected = chain_hash(previous, body)
        if event.prev_hash != previous or event.entry_hash != expected:
            return False, event.id
        previous = event.entry_hash
    return True, None
