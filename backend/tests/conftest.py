"""Test fixtures.

Tests run against a real PostGIS database (``barathseva_test``), not a mock.
The geospatial logic *is* the behaviour under test — ward containment, radius
search and DBSCAN clustering have no meaningful in-memory substitute — so a
fake would only prove the fake works.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Must be set before any app module imports and builds the engine.
TEST_DB_URL = os.environ.setdefault(
    "BARATHSEVA_DATABASE_URL",
    "postgresql+psycopg2://barathseva:barathseva@localhost:55432/barathseva_test",
)
os.environ.setdefault("BARATHSEVA_AI_PROVIDER", "stub")
os.environ.setdefault("BARATHSEVA_MEDIA_ROOT", str(ROOT / "media_test"))
os.environ.setdefault("BARATHSEVA_SECRET_KEY", "test-secret")

import psycopg2  # noqa: E402
import pytest  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.ids import ensure_sequence  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: F401,E402
from app.seed import seed_all  # noqa: E402


def _ensure_test_database() -> None:
    """Create barathseva_test if it does not exist yet."""
    admin = psycopg2.connect(
        host="localhost",
        port=55432,
        user="barathseva",
        password="barathseva",
        dbname="postgres",
    )
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = 'barathseva_test'"
            )
            if cursor.fetchone() is None:
                cursor.execute("CREATE DATABASE barathseva_test")
    finally:
        admin.close()


@pytest.fixture(scope="session", autouse=True)
def database():
    """Build the schema once per session."""
    _ensure_test_database()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        ensure_sequence(session)
        session.commit()
    yield
    engine.dispose()


@pytest.fixture
def db(database):
    """A clean, seeded session per test."""
    with SessionLocal() as session:
        # Truncate transactional tables; reference data is re-seeded below.
        session.execute(
            text(
                "TRUNCATE complaint_events, agent_runs, complaint_evidence, "
                "social_posts, capture_tokens, complaints, users, wards, "
                "departments, sla_policies RESTART IDENTITY CASCADE"
            )
        )
        session.execute(text("ALTER SEQUENCE complaint_reference_seq RESTART WITH 1"))
        session.commit()
        seed_all(session)
        yield session
        session.rollback()


@pytest.fixture
def citizen(db):
    from app.models import User

    return db.query(User).filter_by(phone="+919000000001").one()


@pytest.fixture
def photo():
    """Factory for synthetic JPEGs with real EXIF."""
    from make_test_photo import make_photo

    return make_photo


KORAMANGALA = (12.9352, 77.6245)
INDIRANAGAR = (12.9719, 77.6412)
JAYANAGAR = (12.9250, 77.5938)
MUMBAI = (19.0760, 72.8777)
