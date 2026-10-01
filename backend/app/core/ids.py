"""Public complaint references.

A citizen-visible reference must be stable, short and sequential. It comes from
a dedicated PostgreSQL sequence rather than the primary key so the two can
diverge without breaking either.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

SEQUENCE_NAME = "complaint_reference_seq"
PREFIX = "BRS"


def ensure_sequence(db: Session) -> None:
    db.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE_NAME} START WITH 1"))


def next_complaint_reference(db: Session) -> str:
    """e.g. ``BRS-000001``. Allocated before insert, so one write per complaint."""
    value = db.execute(text(f"SELECT nextval('{SEQUENCE_NAME}')")).scalar_one()
    return f"{PREFIX}-{int(value):06d}"
