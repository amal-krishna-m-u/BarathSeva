"""Shared API dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import settings


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
