"""Idempotent seed of the deterministic reference data.

Wards, departments and SLA policies are configuration, not user content: the
pipeline cannot route or compute a deadline without them.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.city import (
    DEPARTMENT_SEEDS,
    SLA_RESOLUTION_HOURS,
    WARD_SEEDS,
    ward_polygon_wkt,
)
from app.core.enums import ComplaintCategory, Priority, UserRole
from app.models import Department, SLAPolicy, User, Ward


def seed_wards(db: Session) -> int:
    created = 0
    for s in WARD_SEEDS:
        existing = db.query(Ward).filter_by(ward_number=s.ward_number).one_or_none()
        wkt = ward_polygon_wkt(s)
        if existing:
            existing.name, existing.zone = s.name, s.zone
            db.execute(
                text(
                    "UPDATE wards SET boundary = ST_GeomFromText(:wkt, 4326) "
                    "WHERE id = :id"
                ),
                {"wkt": wkt, "id": existing.id},
            )
            continue
        db.add(
            Ward(
                ward_number=s.ward_number,
                name=s.name,
                zone=s.zone,
                city="Bengaluru",
                boundary=f"SRID=4326;{wkt}",
            )
        )
        created += 1
    db.flush()
    return created


def seed_departments(db: Session) -> int:
    created = 0
    for d in DEPARTMENT_SEEDS:
        existing = db.query(Department).filter_by(code=d["code"]).one_or_none()
        if existing:
            existing.name = d["name"]
            existing.full_name = d["full_name"]
            existing.categories = d["categories"]
            existing.contact_email = d["contact_email"]
            existing.api_base_url = d["api_base_url"]
            existing.is_mock = True
            continue
        db.add(Department(is_mock=True, **d))
        created += 1
    db.flush()
    return created


def seed_sla_policies(db: Session) -> int:
    created = 0
    for category, by_priority in SLA_RESOLUTION_HOURS.items():
        for priority, hours in by_priority.items():
            existing = (
                db.query(SLAPolicy)
                .filter_by(category=category, priority=priority)
                .one_or_none()
            )
            escalate = max(1, round(hours * 0.6))
            desc = (
                f"{category} at {priority}: resolve within {hours}h, "
                f"escalate if untouched after {escalate}h."
            )
            if existing:
                existing.resolution_hours = hours
                existing.escalation_hours = escalate
                existing.description = desc
                continue
            db.add(
                SLAPolicy(
                    category=ComplaintCategory(category),
                    priority=Priority(priority),
                    resolution_hours=hours,
                    escalation_hours=escalate,
                    description=desc,
                )
            )
            created += 1
    db.flush()
    return created


def seed_demo_users(db: Session) -> int:
    """A citizen and an officer so the prototype has actors to attribute to."""
    created = 0
    demo = [
        {
            "display_name": "Demo Citizen",
            "phone": "+919000000001",
            "role": UserRole.CITIZEN,
            "is_verified": True,
            "trust_score": 0.6,
        },
        {
            "display_name": "Ward Officer",
            "phone": "+919000000002",
            "role": UserRole.OFFICER,
            "is_verified": True,
            "trust_score": 1.0,
        },
    ]
    for u in demo:
        if db.query(User).filter_by(phone=u["phone"]).one_or_none():
            continue
        db.add(User(**u))
        created += 1
    db.flush()
    return created


def seed_all(db: Session) -> dict[str, int]:
    result = {
        "wards": seed_wards(db),
        "departments": seed_departments(db),
        "sla_policies": seed_sla_policies(db),
        "users": seed_demo_users(db),
    }
    db.commit()
    return result
