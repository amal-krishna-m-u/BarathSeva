"""Department routing — deterministic by design.

A department mapping is a lookup that must be correct every time: a misrouted
complaint is worse than an unrouted one. No model participates in this
decision.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ComplaintCategory
from app.models import Department

#: Fallback used only when the departments table has no owner for a category.
DEFAULT_DEPARTMENT_CODE = "BBMP"


def resolve_department(
    db: Session, category: Optional[ComplaintCategory | str]
) -> tuple[Optional[Department], str]:
    """Return (department, reason). Reason is recorded in the audit trail."""
    if category is None:
        value = ComplaintCategory.OTHER.value
    else:
        value = category.value if hasattr(category, "value") else str(category)

    departments = db.execute(select(Department)).scalars().all()
    for dept in departments:
        if value in (dept.categories or []):
            return dept, (
                f"Category {value} is owned by {dept.code} "
                f"({dept.full_name}) in the departments routing table."
            )

    fallback = next(
        (d for d in departments if d.code == DEFAULT_DEPARTMENT_CODE), None
    )
    if fallback:
        return fallback, (
            f"No department claims category {value}; routed to the municipal "
            f"default {fallback.code}."
        )
    return None, f"No department configured for category {value}."
