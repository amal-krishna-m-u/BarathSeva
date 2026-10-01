"""Liveness and configuration introspection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.factory import get_provider
from app.config import settings
from app.db import get_db

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Reports what is actually wired up, so a demo never misrepresents itself."""
    checks: dict[str, Any] = {}

    try:
        postgis_version = db.execute(text("SELECT postgis_version()")).scalar_one()
        checks["database"] = {"ok": True, "postgis": postgis_version}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}

    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        checks["redis"] = {"ok": bool(client.ping())}
    except Exception as exc:
        checks["redis"] = {"ok": False, "error": str(exc)}

    provider = get_provider()
    checks["ai"] = {
        "provider": provider.name,
        "model": provider.model,
        "is_real_model": provider.is_ai,
        "note": (
            "Running the deterministic rule-based provider. Set "
            "BARATHSEVA_AI_PROVIDER=openai|gemini with a key for model-backed "
            "reasoning."
            if not provider.is_ai
            else "Model-backed provider active."
        ),
    }

    from app.integrations.telegram import client as telegram_client

    checks["telegram"] = {"enabled": telegram_client.enabled}
    checks["government_apis"] = {
        "mode": "mock",
        "departments": ["BBMP", "BWSSB", "BESCOM"],
        "note": "No real municipal integrations. Mock endpoints only.",
    }
    checks["admin_auth"] = {
        "shared_key_enforced": settings.require_admin_key,
        "note": (
            "Prototype shared-key gate. Not real authorization — production "
            "needs per-officer identity and roles."
        ),
    }

    overall = checks["database"]["ok"]
    return {
        "status": "ok" if overall else "degraded",
        "app": settings.app_name,
        "city": settings.city,
        "environment": settings.environment,
        "checks": checks,
    }


@router.get("/api/config")
def public_config() -> dict[str, Any]:
    """Client-visible policy values, so the UI explains the same thresholds the
    backend enforces instead of hardcoding its own copy."""
    return {
        "city": settings.city,
        "capture_token_ttl_seconds": settings.capture_token_ttl_seconds,
        "gps_accuracy_max_meters": settings.gps_accuracy_max_meters,
        "authenticity_auto_accept": settings.authenticity_auto_accept,
        "authenticity_review_floor": settings.authenticity_review_floor,
        "cluster_radius_meters": settings.cluster_radius_meters,
        "cluster_window_hours": settings.cluster_window_hours,
        "hotspot_min_complaints": settings.hotspot_min_complaints,
        "categories": [
            "POTHOLE", "ROAD_DAMAGE", "WATER_LEAK", "PIPELINE_BURST", "DRAINAGE",
            "SEWAGE", "GARBAGE", "STREETLIGHT", "POWER_OUTAGE", "OTHER",
        ],
        "priorities": ["P1", "P2", "P3", "P4"],
        "statuses": [
            "SUBMITTED", "PENDING_REVIEW", "REJECTED", "VERIFIED", "CLASSIFIED",
            "LOCATED", "DISPATCHED", "IN_PROGRESS", "RESOLVED", "ESCALATED",
        ],
    }
