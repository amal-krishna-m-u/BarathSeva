"""Idempotent seed of the deterministic reference data.

Wards, departments and SLA policies are configuration, not user content: the
pipeline cannot route or compute a deadline without them.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

import logging
import os

from app.config import settings
from app.core.auth import hash_password
from app.core.city import (
    DEFAULT_DEMO_PASSWORD,
    DEMO_ACCOUNTS,
    DEMO_PASSWORD_ENV,
    DEPARTMENT_SEEDS,
    SLA_RESOLUTION_HOURS,
    WARD_SEEDS,
    ward_polygon_wkt,
)
from app.core.enums import ComplaintCategory, Priority, UserRole
from app.models import Department, SLAPolicy, User, Ward, utcnow

logger = logging.getLogger(__name__)


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
            existing.service_label = d["service_label"]
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


def seed_demo_accounts(db: Session) -> int:
    """Seed the demo logins: one super admin, one admin per department, one
    citizen.

    Skipped entirely when ``environment`` is production, so a real deployment
    can never inherit a published credential. The password is read from
    ``BARATHSEVA_DEMO_PASSWORD`` and falls back to the documented default only
    outside production.
    """
    if settings.environment.strip().lower() in {"production", "prod"}:
        logger.warning("environment=production: demo accounts were NOT seeded")
        return 0

    password = os.environ.get(DEMO_PASSWORD_ENV) or DEFAULT_DEMO_PASSWORD
    departments = {d.code: d for d in db.query(Department).all()}
    created = 0

    for account in DEMO_ACCOUNTS:
        email = account["email"]
        existing = db.query(User).filter_by(email=email).one_or_none()
        department = departments.get(account["department_code"] or "")

        if existing is not None:
            # Keep the demo set usable across re-seeds without clobbering
            # anything a developer changed deliberately.
            existing.role = UserRole(account["role"])
            existing.department_id = department.id if department else None
            existing.is_active = True
            continue

        db.add(
            User(
                display_name=account["display_name"],
                email=email,
                phone=account.get("phone"),
                role=UserRole(account["role"]),
                department_id=department.id if department else None,
                password_hash=hash_password(password),
                is_active=True,
                is_verified=True,
                trust_score=1.0 if account["role"] != "CITIZEN" else 0.6,
                credentials_changed_at=utcnow(),
            )
        )
        created += 1

    db.flush()
    return created


def seed_all(db: Session) -> dict[str, int]:
    result = {
        "wards": seed_wards(db),
        "departments": seed_departments(db),
        "sla_policies": seed_sla_policies(db),
        "accounts": seed_demo_accounts(db),
    }
    db.commit()
    return result
