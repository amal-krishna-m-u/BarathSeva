"""Provider API keys set from the admin dashboard.

The point of this feature is that an operator can point the deployment at a
model without shell access, so these tests care about three things: the key is
never readable back, a stored key actually overrides the environment, and a
pasted key can be proven to work before a citizen's complaint depends on it.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai import credentials
from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.models import ProviderCredential


class TestCrypto:
    def test_round_trip(self):
        assert decrypt_secret(encrypt_secret("nvapi-secret-value")) == "nvapi-secret-value"

    def test_ciphertext_does_not_contain_the_plaintext(self):
        assert "nvapi-secret-value" not in encrypt_secret("nvapi-secret-value")

    def test_garbage_decrypts_to_none_rather_than_raising(self):
        """A rotated secret key must degrade, not crash every complaint."""
        assert decrypt_secret("not-a-fernet-token") is None

    def test_mask_shows_only_the_tail(self):
        masked = mask_secret("AQ.Ab8RN6SuperSecretTail")
        assert masked.endswith("Tail")
        assert "SuperSecret" not in masked

    def test_mask_of_nothing_is_nothing(self):
        assert mask_secret(None) is None
        assert mask_secret("") is None


class TestResolution:
    def test_environment_is_used_when_no_row_exists(self, db, monkeypatch):
        monkeypatch.setattr(credentials.settings, "gemini_api_key", "env-key", raising=False)
        resolved = credentials.resolve(db, "gemini")
        assert resolved.api_key == "env-key"
        assert resolved.source == "environment"

    def test_a_stored_row_overrides_the_environment(self, db, monkeypatch):
        monkeypatch.setattr(credentials.settings, "gemini_api_key", "env-key", raising=False)
        db.add(
            ProviderCredential(
                provider="gemini",
                api_key_encrypted=encrypt_secret("dashboard-key"),
                model="gemini-3.8-flash",
            )
        )
        db.commit()
        resolved = credentials.resolve(db, "gemini")
        assert resolved.api_key == "dashboard-key"
        assert resolved.model == "gemini-3.8-flash"
        assert resolved.source == "database"

    def test_undecryptable_row_falls_back_to_the_environment(self, db, monkeypatch):
        monkeypatch.setattr(credentials.settings, "gemini_api_key", "env-key", raising=False)
        db.add(ProviderCredential(provider="gemini", api_key_encrypted="corrupt"))
        db.commit()
        resolved = credentials.resolve(db, "gemini")
        assert resolved.api_key == "env-key"
        assert resolved.source == "environment"

    def test_active_row_selects_the_live_provider(self, db, monkeypatch):
        monkeypatch.setattr(credentials.settings, "ai_provider", "stub", raising=False)
        assert credentials.active_provider(db) == "stub"
        db.add(ProviderCredential(provider="nvidia", is_active=True))
        db.commit()
        assert credentials.active_provider(db) == "nvidia"


class TestAdminApi:
    def test_requires_super_admin(self, client, as_citizen):
        assert client.get("/api/admin/ai-providers").status_code in (401, 403)
        assert as_citizen.get("/api/admin/ai-providers").status_code == 403

    def test_listing_never_returns_a_usable_key(self, as_super_admin, db):
        as_super_admin.put(
            "/api/admin/ai-providers/gemini",
            json={"api_key": "AQ.VerySecretKeyValue", "model": "gemini-3.8-flash"},
        ).raise_for_status()

        body = as_super_admin.get("/api/admin/ai-providers").json()
        gemini = [p for p in body if p["provider"] == "gemini"][0]
        assert gemini["configured"] is True
        assert gemini["model"] == "gemini-3.8-flash"
        assert "VerySecretKeyValue" not in str(body)
        assert gemini["masked_key"].endswith("alue")

    def test_key_is_encrypted_in_the_database(self, as_super_admin, db):
        as_super_admin.put(
            "/api/admin/ai-providers/nvidia", json={"api_key": "nvapi-plaintext-check"}
        ).raise_for_status()
        row = db.query(ProviderCredential).filter_by(provider="nvidia").one()
        assert "nvapi-plaintext-check" not in (row.api_key_encrypted or "")
        assert decrypt_secret(row.api_key_encrypted) == "nvapi-plaintext-check"

    def test_model_can_be_changed_without_resending_the_key(self, as_super_admin, db):
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "keep-me", "model": "model-a"}
        ).raise_for_status()
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"model": "model-b"}
        ).raise_for_status()

        row = db.query(ProviderCredential).filter_by(provider="gemini").one()
        assert row.model == "model-b"
        assert decrypt_secret(row.api_key_encrypted) == "keep-me"

    def test_empty_key_clears_it(self, as_super_admin, db):
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "to-be-cleared"}
        ).raise_for_status()
        as_super_admin.put("/api/admin/ai-providers/gemini", json={"api_key": ""}).raise_for_status()
        row = db.query(ProviderCredential).filter_by(provider="gemini").one()
        assert row.api_key_encrypted is None

    def test_activating_one_provider_deactivates_the_others(self, as_super_admin, db):
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "k", "make_active": True}
        ).raise_for_status()
        as_super_admin.put(
            "/api/admin/ai-providers/nvidia", json={"api_key": "k", "make_active": True}
        ).raise_for_status()

        active = [r.provider for r in db.query(ProviderCredential).filter_by(is_active=True).all()]
        assert active == ["nvidia"]

    def test_unknown_provider_is_404(self, as_super_admin):
        assert as_super_admin.put("/api/admin/ai-providers/skynet", json={}).status_code == 404

    def test_delete_falls_back_to_the_environment(self, as_super_admin, db, monkeypatch):
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "dashboard"}
        ).raise_for_status()
        body = as_super_admin.delete("/api/admin/ai-providers/gemini").json()
        assert db.query(ProviderCredential).filter_by(provider="gemini").one_or_none() is None
        assert body["source"] in ("environment", "none")

    def test_test_endpoint_reports_not_configured(self, as_super_admin, monkeypatch):
        monkeypatch.setattr(credentials.settings, "openai_api_key", None, raising=False)
        body = as_super_admin.post("/api/admin/ai-providers/openai/test").json()
        assert body["ok"] is False
        assert body["error_kind"] == "NOT_CONFIGURED"

    def test_test_endpoint_distinguishes_a_bad_key(self, as_super_admin, monkeypatch):
        """A wrong key must read as AUTH, not as a generic failure.

        The provider is built through a MockTransport rather than by patching
        httpx globally: the TestClient speaks httpx too, so a global patch
        intercepts the test's own request instead of the provider's.
        """
        from app.ai.gemini_provider import GeminiProvider
        from app.api import admin as admin_module

        def unauthorized(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "API key not valid"}})

        monkeypatch.setattr(
            admin_module,
            "_build_test_provider",
            lambda provider, resolved: GeminiProvider(
                resolved.api_key, resolved.model, transport=httpx.MockTransport(unauthorized)
            ),
        )
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "wrong-key"}
        ).raise_for_status()

        body = as_super_admin.post("/api/admin/ai-providers/gemini/test").json()
        assert body["ok"] is False
        assert body["error_kind"] == "AUTH"

    def test_test_endpoint_reports_a_working_key(self, as_super_admin, monkeypatch):
        from app.ai.gemini_provider import GeminiProvider
        from app.api import admin as admin_module

        def ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]},
            )

        monkeypatch.setattr(
            admin_module,
            "_build_test_provider",
            lambda provider, resolved: GeminiProvider(
                resolved.api_key, resolved.model, transport=httpx.MockTransport(ok)
            ),
        )
        as_super_admin.put(
            "/api/admin/ai-providers/gemini", json={"api_key": "good-key"}
        ).raise_for_status()

        body = as_super_admin.post("/api/admin/ai-providers/gemini/test").json()
        assert body["ok"] is True
        assert body["error_kind"] is None
