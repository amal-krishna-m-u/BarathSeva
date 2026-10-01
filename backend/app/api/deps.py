"""Shared API dependencies."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from app.config import settings
from app.core.auth import get_current_user_optional
from app.models import User


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Shared-key gate for officer endpoints.

    This is a prototype control, not real authorization. Production requires
    per-officer identity, role checks and an access audit — see "Prototype vs
    Production" in the README. Enforcement is off by default so the local demo
    runs without configuration, and MUST be enabled outside localhost by
    setting ``BARATHSEVA_REQUIRE_ADMIN_KEY=true``.
    """
    if not settings.require_admin_key:
        return
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Key header.",
        )


def reporter_only(
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> Optional[User]:
    """Resolve the caller for intake, refusing staff accounts.

    Reporting is a citizen action. The department desk and the command center
    exist to triage and resolve what citizens report, and a staff account
    filing its own complaint corrupts the one real signal the system has about
    who reported what — it would appear in the same feed, counted the same way,
    and be indistinguishable later from a genuine citizen report.

    Drop-in replacement for ``get_current_user_optional`` on intake routes: it
    returns the same value for every caller that is still allowed, so
    ``None`` (anonymous) keeps working. Anonymous reporting is deliberately
    left open — this gate removes the staff-account reporting path, and is not
    identity-proofing: a staff member who signs out can still report like any
    member of the public, which is the access trade-off the project chose.
    """
    if current_user is not None and current_user.role.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Staff accounts cannot file reports. Sign in as a citizen, or "
                "report without signing in."
            ),
        )
    return current_user
