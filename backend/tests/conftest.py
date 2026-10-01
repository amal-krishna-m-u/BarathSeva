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
# Minimum bcrypt cost: these tests exercise auth logic, not key-stretching.
os.environ.setdefault("BARATHSEVA_BCRYPT_ROUNDS", "4")
os.environ.setdefault("BARATHSEVA_DEMO_PASSWORD", "TestPassw0rd!")

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


@pytest.fixture(autouse=True)
def _clear_login_throttle():
    """Reset the login throttle between tests.

    The throttle is Redis-backed with a 15-minute window, so failed-login
    counters survive the process. Without this, tests that deliberately submit
    bad passwords would poison later runs: the counter creeps up across
    invocations until an unrelated login starts returning 429.
    """
    from app.core.auth import throttle

    if throttle._redis is not None:  # noqa: SLF001 - test-only cleanup
        try:
            for key in throttle._redis.scan_iter("barathseva:login_fail:*"):
                throttle._redis.delete(key)
        except Exception:
            pass
    throttle._memory.clear()
    yield


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


DEMO_PASSWORD = os.environ["BARATHSEVA_DEMO_PASSWORD"]


def _new_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def client(db):
    """Unauthenticated HTTP client."""
    with _new_client() as test_client:
        yield test_client


def _authenticated_client(email: str):
    """Build a client carrying its own session.

    Each role gets a SEPARATE TestClient instance. Sharing one and swapping the
    Authorization header looks equivalent but is not: a test requesting two
    role fixtures would get the same object, and the second sign-in would
    silently overwrite the first — so a "cross-department" assertion would
    actually be testing one department against itself.
    """
    test_client = _new_client()
    response = test_client.post(
        "/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    test_client.headers["Authorization"] = f"Bearer {token}"
    return test_client


@pytest.fixture
def as_super_admin(db):
    """Client authenticated as the city-wide super admin."""
    with _authenticated_client("admin@example.com") as test_client:
        yield test_client


@pytest.fixture
def as_water_admin(db):
    """Client authenticated as the BWSSB department admin."""
    with _authenticated_client("water@example.com") as test_client:
        yield test_client


@pytest.fixture
def as_power_admin(db):
    """Client authenticated as the BESCOM department admin."""
    with _authenticated_client("power@example.com") as test_client:
        yield test_client


@pytest.fixture
def as_roads_admin(db):
    """Client authenticated as the BBMP department admin."""
    with _authenticated_client("roads@example.com") as test_client:
        yield test_client


@pytest.fixture
def as_citizen(db):
    """Client authenticated as a demo citizen."""
    with _authenticated_client("citizen@example.com") as test_client:
        yield test_client
