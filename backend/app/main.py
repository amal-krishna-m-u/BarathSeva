"""FastAPI application — the single API surface and trust boundary.

Request validation, authorization and rate limits belong here rather than
inside the agents, so a node can assume it is operating on a well-formed,
already-persisted complaint.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, complaints, department, geocode, health, telegram
from app.config import settings
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


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
