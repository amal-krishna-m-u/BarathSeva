"""FastAPI application — the single API surface and trust boundary.

Request validation, authorization and rate limits belong here rather than
inside the agents, so a node can assume it is operating on a well-formed,
already-persisted complaint.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, complaints, department, geocode, health, telegram
from app.config import Settings, settings
from app.core.ids import ensure_sequence
from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DESCRIPTION = """
Autonomous civic complaint orchestration for Bengaluru.

One citizen message — text, a photo and a location — is enough to start the
complete workflow: evidence authentication, verification, classification,
ward mapping, cluster detection, department routing, ticket creation, SLA
monitoring and resolution.

## Access

- `/api/auth/*` — registration and sign-in. Self-registration creates citizens
  only; staff accounts are provisioned by seeding or a super admin.
- `/api/department/*` — a department admin's inbox, scoped server-side to that
  one agency.
- `/api/admin/*` — city-wide view, super admins only.

**Prototype scope.** Government connectivity is served by mock BBMP / BWSSB /
BESCOM endpoints. There are no real municipal integrations. The default AI
provider is a deterministic rule engine so the platform runs without API keys;
set `BARATHSEVA_AI_PROVIDER` to use a real model.
"""

app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(geocode.router)
app.include_router(complaints.router)
app.include_router(department.router)
app.include_router(admin.router)
app.include_router(telegram.router)


def register_mock_routers(app: FastAPI, settings_obj: Optional[Settings] = None) -> None:
    """Register every mock-surface router, gated on mock_apis_enabled.

    Mock surfaces simulate third-party systems (an LLM provider today; the
    mock government gateway and others join this same function in later
    tasks) so the platform is fully runnable and testable with no API keys
    and no live credentials. They must be impossible to expose in production
    even if an operator leaves ``enable_mock_apis=true`` in a shared config,
    so the gate is the ``mock_apis_enabled`` *property* (which also checks
    ``environment``), never the raw flag.

    Routers are bound at import time in FastAPI, so a bare
    ``if settings.mock_apis_enabled: app.include_router(...)`` at module
    scope could only be tested by reload-ing ``app.main`` — fragile and
    order-dependent across a long test session. Putting the check in a
    function instead lets a test build a throwaway ``FastAPI()``, construct
    a ``Settings(environment="production", ...)`` directly (as
    ``tests/test_config.py`` already does) and pass it in via
    ``settings_obj`` to assert the routes are absent, with no monkeypatching
    and no cross-test state.

    Called once at module scope below for the real app, using the process's
    actual ``settings``.
    """
    settings_obj = settings_obj or settings
    if not settings_obj.mock_apis_enabled:
        return

    from app.api import mock_gov_api, mock_llm

    app.include_router(mock_llm.router)
    app.include_router(mock_gov_api.router)
    # Task 11 registers its router here too — keep this the single place
    # mock surfaces are wired up.


register_mock_routers(app)


@app.on_event("startup")
def on_startup() -> None:
    with SessionLocal() as db:
        ensure_sequence(db)
        db.commit()
    logger.info(
        "%s ready | city=%s | ai_provider=%s | admin_key_enforced=%s",
        settings.app_name,
        settings.city,
        settings.ai_provider,
        settings.require_admin_key,
    )
    # Degrade, don't refuse to boot: a misconfigured provider or secret must
    # not stop citizen intake, which still has the stub pipeline to fall
    # back on. validate_runtime() never raises.
    for warning in settings.validate_runtime():
        logger.warning(warning)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
