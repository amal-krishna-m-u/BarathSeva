"""Authentication, roles, and department scoping.

The scoping tests matter most: they assert that a department admin cannot see
or act on another agency's complaints. That is the property the whole portal
rests on, and it is enforced server-side, so it has to be tested server-side.
"""

from __future__ import annotations

import time
from datetime import timedelta

import jwt
import pytest

from app.config import settings
from app.core.auth import create_access_token, hash_password, throttle, verify_password
from app.core.enums import UserRole
from app.core.security import issue_capture_token
from app.models import User, utcnow
from tests.conftest import DEMO_PASSWORD, INDIRANAGAR, JAYANAGAR, KORAMANGALA


def submit_complaint_via_api(test_client, photo, description, location, seed=1):
    """File a complaint through the real HTTP path."""
    token = test_client.post(
        "/api/capture-token",
        json={"latitude": location[0], "longitude": location[1]},
    ).json()["token"]
    response = test_client.post(
        "/api/complaints",
        data={
            "description": description,
            "latitude": str(location[0]),
            "longitude": str(location[1]),
            "capture_token": token,
            "gps_accuracy_meters": "8",
            "location_source": "device_gps",
        },
        files={"photo": ("e.jpg", photo(*location, seed=seed), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ registration
class TestRegistration:
    def test_register_returns_a_session(self, client):
        response = client.post(
            "/api/auth/register",
            json={
                "email": "newcitizen@example.com",
                "password": "a-good-long-password",
                "display_name": "New Citizen",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["access_token"]
        assert body["user"]["role"] == "CITIZEN"

    def test_self_registration_cannot_grant_privilege(self, client, db):
        """A role in the payload must be ignored, not honoured."""
        response = client.post(
            "/api/auth/register",
            json={
                "email": "sneaky@example.com",
                "password": "a-good-long-password",
                "display_name": "Sneaky",
                "role": "SUPER_ADMIN",
                "department_id": 1,
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["role"] == "CITIZEN"

        user = db.query(User).filter_by(email="sneaky@example.com").one()
        assert user.role is UserRole.CITIZEN
        assert user.department_id is None

    def test_duplicate_email_rejected(self, client):
        payload = {
            "email": "dupe@example.com",
            "password": "a-good-long-password",
            "display_name": "First",
        }
        assert client.post("/api/auth/register", json=payload).status_code == 201
        assert client.post("/api/auth/register", json=payload).status_code == 409

    def test_short_password_rejected(self, client):
        response = client.post(
            "/api/auth/register",
            json={
                "email": "weak@example.com",
                "password": "short",
                "display_name": "Weak",
            },
        )
        assert response.status_code == 422

    def test_registering_with_a_known_phone_claims_prior_reports(
        self, client, db, photo
    ):
        """An anonymous reporter who later signs up keeps their history."""
        created = submit_complaint_via_api(
            client,
            photo,
            "Pothole near the junction, quite deep",
            KORAMANGALA,
            seed=611,
        )
        # Attribute that complaint to a bare phone-only user.
        from app.models import Complaint

        complaint = (
            db.query(Complaint).filter_by(reference=created["reference"]).one()
        )
        anonymous = User(display_name="Anon", phone="+919888800011")
        db.add(anonymous)
        db.flush()
        complaint.reporter_id = anonymous.id
        db.commit()

        response = client.post(
            "/api/auth/register",
            json={
                "email": "claimed@example.com",
                "password": "a-good-long-password",
                "display_name": "Claimed Citizen",
                "phone": "+919888800011",
            },
        )
        assert response.status_code == 201
        token = response.json()["access_token"]

        mine = client.get(
            "/api/me/complaints", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert created["reference"] in {row["reference"] for row in mine}


# ------------------------------------------------------------------------ login
class TestLogin:
    def test_each_seeded_role_can_sign_in(self, client):
        expected = {
            "admin@example.com": ("SUPER_ADMIN", None),
            "water@example.com": ("DEPT_ADMIN", "BWSSB"),
            "power@example.com": ("DEPT_ADMIN", "BESCOM"),
            "roads@example.com": ("DEPT_ADMIN", "BBMP"),
            "citizen@example.com": ("CITIZEN", None),
        }
        for email, (role, department) in expected.items():
            body = client.post(
                "/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}
            ).json()
            assert body["user"]["role"] == role
            actual = body["user"]["department"]
            assert (actual["code"] if actual else None) == department

    def test_wrong_password_is_generic(self, client):
        response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password."

    def test_unknown_email_is_indistinguishable(self, client):
        """Login must not reveal which addresses are registered."""
        unknown = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "whatever-long"},
        )
        known = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "wrong-password"},
        )
        assert unknown.status_code == known.status_code == 401
        assert unknown.json()["detail"] == known.json()["detail"]

    def test_repeated_failures_are_throttled(self, client):
        email = "throttle-target@example.com"
        throttle.clear(f"{email}|testclient")
        for _ in range(settings.login_max_attempts):
            client.post(
                "/api/auth/login", json={"email": email, "password": "bad-password"}
            )
        blocked = client.post(
            "/api/auth/login", json={"email": email, "password": "bad-password"}
        )
        assert blocked.status_code == 429
        throttle.clear(f"{email}|testclient")

    def test_disabled_account_cannot_sign_in(self, client, db):
        user = db.query(User).filter_by(email="citizen@example.com").one()
        user.is_active = False
        db.commit()
        response = client.post(
            "/api/auth/login",
            json={"email": "citizen@example.com", "password": DEMO_PASSWORD},
        )
        assert response.status_code == 403


# ------------------------------------------------------------------- tokens
class TestTokens:
    def test_me_requires_authentication(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_forged_token_rejected(self, client):
        response = client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not.a.token"}
        )
        assert response.status_code == 401

    def test_token_signed_with_another_key_rejected(self, client, db):
        user = db.query(User).filter_by(email="admin@example.com").one()
        forged = jwt.encode(
            {"sub": str(user.id), "role": "SUPER_ADMIN", "tv": 1,
             "exp": int((utcnow() + timedelta(hours=1)).timestamp())},
            "a-different-secret",
            algorithm="HS256",
        )
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401

    def test_expired_token_rejected(self, client, db, monkeypatch):
        user = db.query(User).filter_by(email="citizen@example.com").one()
        monkeypatch.setattr(settings, "access_token_ttl_minutes", -1)
        expired = create_access_token(user).access_token
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401

    def test_changing_password_invalidates_existing_tokens(self, as_citizen, client):
        # `client` is a separate instance, so it can replay the old token after
        # `as_citizen` has rotated to a new one.
        old_token = as_citizen.headers["Authorization"]
        assert as_citizen.get("/api/auth/me").status_code == 200

        changed = as_citizen.post(
            "/api/auth/change-password",
            json={
                "current_password": DEMO_PASSWORD,
                "new_password": "a-brand-new-password",
            },
        )
        assert changed.status_code == 200

        # The old bearer token must no longer work.
        assert client.get(
            "/api/auth/me", headers={"Authorization": old_token}
        ).status_code == 401
        # The freshly issued one must.
        new_token = changed.json()["access_token"]
        assert client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}
        ).status_code == 200

    def test_wrong_current_password_rejected(self, as_citizen):
        response = as_citizen.post(
            "/api/auth/change-password",
            json={"current_password": "not-it", "new_password": "a-new-password-1"},
        )
        assert response.status_code == 401


# ----------------------------------------------------------- role boundaries
class TestRoleBoundaries:
    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/complaints"])
    def test_anonymous_cannot_reach_admin(self, client, path):
        assert client.get(path).status_code == 401

    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/complaints"])
    def test_citizen_cannot_reach_admin(self, as_citizen, path):
        assert as_citizen.get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/complaints"])
    def test_department_admin_cannot_reach_city_admin(self, as_water_admin, path):
        """A department desk is not a city-wide console."""
        assert as_water_admin.get(path).status_code == 403

    def test_super_admin_can_reach_admin(self, as_super_admin):
        assert as_super_admin.get("/api/admin/stats").status_code == 200

    def test_citizen_cannot_reach_department_portal(self, as_citizen):
        assert as_citizen.get("/api/department/complaints").status_code == 403

    def test_anonymous_cannot_reach_department_portal(self, client):
        assert client.get("/api/department/complaints").status_code == 401

    def test_my_complaints_requires_authentication(self, client):
        assert client.get("/api/me/complaints").status_code == 401

    def test_my_complaints_is_scoped_to_the_session(self, as_citizen, client, photo):
        """A citizen sees their own reports and no one else's."""
        mine = submit_complaint_via_api(
            as_citizen,
            photo,
            "Pothole outside my gate, getting deeper",
            KORAMANGALA,
            seed=701,
        )
        # Anonymous report by someone else.
        theirs = submit_complaint_via_api(
            client,
            photo,
            "Garbage pile near the market, not collected",
            JAYANAGAR,
            seed=702,
        )
        references = {row["reference"] for row in as_citizen.get("/api/me/complaints").json()}
        assert mine["reference"] in references
        assert theirs["reference"] not in references


# ------------------------------------------------------- department scoping
class TestDepartmentScoping:
    @pytest.fixture
    def routed(self, client, photo):
        """One complaint per department, filed anonymously."""
        water = submit_complaint_via_api(
            client,
            photo,
            "Water gushing from a burst pipeline, whole street flooded",
            INDIRANAGAR,
            seed=801,
        )
        power = submit_complaint_via_api(
            client,
            photo,
            "No electricity since morning, transformer sparking, dangerous",
            JAYANAGAR,
            seed=802,
        )
        assert water["department_code"] == "BWSSB"
        assert power["department_code"] == "BESCOM"
        return {"BWSSB": water["reference"], "BESCOM": power["reference"]}

    def test_each_department_sees_only_its_own(self, routed, as_water_admin):
        rows = as_water_admin.get("/api/department/complaints").json()
        assert {row["department_code"] for row in rows} == {"BWSSB"}
        assert routed["BWSSB"] in {row["reference"] for row in rows}
        assert routed["BESCOM"] not in {row["reference"] for row in rows}

    def test_cross_department_read_is_404(self, routed, as_water_admin):
        """Indistinguishable from "does not exist" — no probing other agencies."""
        response = as_water_admin.get(
            f"/api/department/complaints/{routed['BESCOM']}"
        )
        assert response.status_code == 404

    def test_own_department_read_succeeds(self, routed, as_water_admin):
        response = as_water_admin.get(
            f"/api/department/complaints/{routed['BWSSB']}"
        )
        assert response.status_code == 200

    def test_cross_department_resolve_is_404(self, routed, as_water_admin):
        """Scoping must cover writes, not just reads."""
        response = as_water_admin.post(
            f"/api/department/complaints/{routed['BESCOM']}/resolve",
            json={"resolution_note": "not mine", "field_outcome": "GENUINE_FIXED"},
        )
        assert response.status_code == 404

    def test_cross_department_acknowledge_is_404(self, routed, as_water_admin):
        response = as_water_admin.post(
            f"/api/department/complaints/{routed['BESCOM']}/acknowledge",
            json={"note": "not mine"},
        )
        assert response.status_code == 404

    def test_department_stats_are_scoped(self, routed, as_water_admin):
        stats = as_water_admin.get("/api/department/stats").json()
        assert stats["department"]["code"] == "BWSSB"
        assert stats["total"] == 1

    def test_acknowledge_then_resolve(self, routed, as_water_admin):
        reference = routed["BWSSB"]
        acknowledged = as_water_admin.post(
            f"/api/department/complaints/{reference}/acknowledge",
            json={"note": "Crew dispatched."},
        ).json()
        assert acknowledged["status"] == "IN_PROGRESS"

        resolved = as_water_admin.post(
            f"/api/department/complaints/{reference}/resolve",
            json={
                "resolution_note": "Pipeline joint replaced.",
                "field_outcome": "GENUINE_FIXED",
            },
        ).json()
        assert resolved["status"] == "RESOLVED"
        assert resolved["resolution_message"]

    def test_double_acknowledge_is_rejected(self, routed, as_water_admin):
        reference = routed["BWSSB"]
        as_water_admin.post(
            f"/api/department/complaints/{reference}/acknowledge", json={"note": ""}
        )
        again = as_water_admin.post(
            f"/api/department/complaints/{reference}/acknowledge", json={"note": ""}
        )
        assert again.status_code == 409

    def test_reassignment_transfers_ownership(
        self, routed, as_water_admin, as_power_admin
    ):
        reference = routed["BWSSB"]
        moved = as_water_admin.post(
            f"/api/department/complaints/{reference}/reassign",
            json={
                "target_department_code": "BESCOM",
                "reason": "The leak is from an electrical substation cooling line.",
            },
        ).json()
        assert moved["department_code"] == "BESCOM"

        # The handing-off department loses access; the receiving one gains it.
        assert as_water_admin.get(
            f"/api/department/complaints/{reference}"
        ).status_code == 404
        assert as_power_admin.get(
            f"/api/department/complaints/{reference}"
        ).status_code == 200

    def test_reassign_to_unknown_department_is_404(self, routed, as_water_admin):
        response = as_water_admin.post(
            f"/api/department/complaints/{routed['BWSSB']}/reassign",
            json={"target_department_code": "NOPE", "reason": "testing the guard"},
        )
        assert response.status_code == 404

    def test_department_admin_without_department_is_blocked(
        self, client, db
    ):
        """A DEPT_ADMIN with no department would otherwise see everything."""
        orphan = User(
            display_name="Orphan Desk",
            email="orphan@example.com",
            role=UserRole.DEPT_ADMIN,
            department_id=None,
            password_hash=hash_password(DEMO_PASSWORD),
            is_active=True,
            credentials_changed_at=utcnow(),
        )
        db.add(orphan)
        db.commit()

        token = client.post(
            "/api/auth/login",
            json={"email": "orphan@example.com", "password": DEMO_PASSWORD},
        ).json()["access_token"]
        response = client.get(
            "/api/department/complaints",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


# ------------------------------------------------------------------ hashing
class TestPasswordHashing:
    def test_hash_is_salted(self):
        assert hash_password("same-password") != hash_password("same-password")

    def test_verify_round_trips(self):
        digest = hash_password("a-reasonable-password")
        assert verify_password("a-reasonable-password", digest)
        assert not verify_password("a-reasonable-passwore", digest)

    def test_long_passwords_are_not_truncated(self):
        """bcrypt caps at 72 bytes; the SHA-256 pre-hash must prevent collisions."""
        base = "x" * 100
        digest = hash_password(base + "ALPHA")
        assert verify_password(base + "ALPHA", digest)
        assert not verify_password(base + "BETA", digest)
