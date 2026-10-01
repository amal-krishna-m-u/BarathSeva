"""Settings behaviour: defaults, the production mock-API gate, and
validate_runtime()'s warnings.

These construct Settings(...) directly rather than mutating process env and
calling get_settings(), because get_settings() is @lru_cache'd and the cache
is already warmed by the time this module imports (conftest.py imports
app.config at collection time).
"""

from __future__ import annotations

from app.config import Settings


def _dev_settings(**overrides) -> Settings:
    """A clean, keyless development config, as a baseline for overrides."""
    defaults = dict(
        environment="development",
        secret_key="dev-insecure-secret-change-me",
        admin_api_key="dev-admin-key",
        ai_provider="stub",
        ai_rate_limit_fallback="gemini",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_defaults_load():
    settings = Settings(_env_file=None)
    assert settings.ai_provider == "stub"
    assert settings.nvidia_base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.nvidia_model == "moonshotai/kimi-k3"
    assert settings.nvidia_api_key is None
    assert settings.ai_rate_limit_fallback == "gemini"
    assert settings.ai_rate_limit_cooldown_seconds == 120
    assert settings.ai_request_timeout_seconds == 20.0
    assert settings.ai_max_image_bytes == 3_000_000
    assert settings.enable_mock_apis is True
    assert settings.gov_gateway_mode == "inprocess"
    assert settings.mock_gov_base_url == "http://localhost:8000/mock/gov"
    assert settings.gov_callback_secret is None
    # Unchanged per task brief: already the Flash model used as fallback.
    assert settings.gemini_model == "gemini-2.0-flash"


def test_mock_apis_enabled_true_in_development():
    settings = _dev_settings(enable_mock_apis=True, environment="development")
    assert settings.mock_apis_enabled is True


def test_mock_apis_enabled_false_in_production_even_if_flag_true():
    settings = _dev_settings(enable_mock_apis=True, environment="production")
    assert settings.mock_apis_enabled is False


def test_mock_apis_disabled_flag_stays_disabled_anywhere():
    settings = _dev_settings(enable_mock_apis=False, environment="development")
    assert settings.mock_apis_enabled is False


def test_validate_runtime_clean_dev_config_has_no_warnings():
    # "Clean" means fully configured: the fallback provider has its key, the
    # primary is the keyless stub, and secrets are still at their (harmless
    # in development) defaults.
    settings = _dev_settings(gemini_api_key="test-key")
    assert settings.validate_runtime() == []


def test_validate_runtime_warns_for_keyless_configured_provider():
    settings = _dev_settings(ai_provider="nvidia", nvidia_api_key=None)
    warnings = settings.validate_runtime()
    assert any("nvidia" in w and "ai_provider" in w for w in warnings)


def test_validate_runtime_warns_for_keyless_rate_limit_fallback():
    settings = _dev_settings(
        ai_provider="stub",
        ai_rate_limit_fallback="gemini",
        gemini_api_key=None,
    )
    warnings = settings.validate_runtime()
    assert any("ai_rate_limit_fallback" in w for w in warnings)


def test_validate_runtime_no_fallback_warning_when_fallback_has_key():
    settings = _dev_settings(
        ai_provider="stub",
        ai_rate_limit_fallback="gemini",
        gemini_api_key="test-key",
    )
    warnings = settings.validate_runtime()
    assert not any("ai_rate_limit_fallback" in w for w in warnings)


def test_validate_runtime_empty_fallback_disables_the_check():
    settings = _dev_settings(ai_rate_limit_fallback="", gemini_api_key=None)
    warnings = settings.validate_runtime()
    assert not any("ai_rate_limit_fallback" in w for w in warnings)


def test_validate_runtime_warns_for_default_secret_key_outside_development():
    settings = _dev_settings(
        environment="production",
        secret_key="dev-insecure-secret-change-me",
    )
    warnings = settings.validate_runtime()
    assert any("secret_key" in w for w in warnings)


def test_validate_runtime_warns_for_default_admin_key_outside_development():
    settings = _dev_settings(
        environment="production",
        admin_api_key="dev-admin-key",
        secret_key="a-real-rotated-secret",
    )
    warnings = settings.validate_runtime()
    assert any("admin_api_key" in w for w in warnings)


def test_validate_runtime_no_default_secret_warnings_in_development():
    settings = _dev_settings(
        environment="development",
        secret_key="dev-insecure-secret-change-me",
        admin_api_key="dev-admin-key",
    )
    warnings = settings.validate_runtime()
    assert not any("secret_key" in w for w in warnings)
    assert not any("admin_api_key" in w for w in warnings)


def test_validate_runtime_never_raises_with_unknown_provider():
    settings = _dev_settings(ai_provider="totally-unknown")
    # Should not raise, and should not report a key-missing warning for an
    # unrecognised provider name (only stub | openai | gemini | nvidia are
    # provider-key-checked).
    warnings = settings.validate_runtime()
    assert isinstance(warnings, list)
