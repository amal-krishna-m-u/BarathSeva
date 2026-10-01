"""Application configuration.

Every value is overridable through the environment with the ``BARATHSEVA_``
prefix (e.g. ``BARATHSEVA_AI_PROVIDER=openai``) or a ``.env`` file.
Policy thresholds live here rather than in agent code so that evidence rules,
SLA behaviour and clustering can be tuned per city without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BARATHSEVA_", env_file=".env", extra="ignore"
    )

    # --- application ---
    app_name: str = "BarathSeva AI"
    environment: str = "development"
    city: str = "Bengaluru"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000"

    # --- infrastructure ---
    database_url: str = (
        "postgresql+psycopg2://barathseva:barathseva@localhost:55432/barathseva"
    )
    redis_url: str = "redis://localhost:56379/0"
    media_root: str = "media"

    # --- secrets ---
    # Signs capture tokens. MUST be replaced outside local development.
    secret_key: str = "dev-insecure-secret-change-me"

    # --- evidence policy (Section 5 of the README) ---
    capture_token_ttl_seconds: int = 300
    exif_time_tolerance_seconds: int = 900
    # Naive EXIF timestamps are interpreted in this offset (IST = 330).
    city_utc_offset_minutes: int = 330
    gps_accuracy_max_meters: float = 100.0
    exif_gps_tolerance_meters: float = 250.0
    # Perceptual-hash bands. An exact SHA-256 match is unambiguous reuse. A
    # perceptual match is only *suspected* reuse, so it is split in two: at or
    # below the hard-fail distance the image is effectively identical (a
    # re-encode or rescale), above it the match is suggestive and routes to a
    # human instead of auto-rejecting a possibly-genuine report.
    phash_duplicate_distance: int = 10
    phash_hard_fail_distance: int = 2
    reporter_max_speed_kmh: float = 120.0
    allow_gallery_uploads: bool = True

    # --- authenticity score bands ---
    authenticity_auto_accept: float = 0.70
    authenticity_review_floor: float = 0.40

    # --- geo clustering ---
    cluster_radius_meters: float = 150.0
    cluster_window_hours: int = 72
    hotspot_min_complaints: int = 4

    # --- ai ---
    ai_provider: str = "stub"  # stub | openai | gemini | nvidia
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: Optional[str] = None
    #: Verified live against the API on 2026-10-01: gemini-2.0-flash and
    #: gemini-2.5-flash both 404 ("no longer available to new users"), and
    #: gemini-3.8-flash / gemini-flash-latest returned 503. This one answers.
    gemini_model: str = "gemini-3-flash-preview"
    #: Tried in order when the configured model returns a retryable failure
    #: (503 / timeout / network). Google's free tier fails per-model and which
    #: model is healthy rotates, so a single-model client holds genuine
    #: complaints whenever its one model is busy.
    gemini_fallback_models: str = "gemini-3.1-flash-lite,gemini-3.8-flash"
    #: Gemini REST host. Only app/ai/gemini_provider.py reads this.
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # --- NVIDIA NIM (Kimi K3) ---
    nvidia_api_key: Optional[str] = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "moonshotai/kimi-k3"

    # --- fallback policy ---
    #: Provider used ONLY when the primary reports a rate limit. Empty disables.
    ai_rate_limit_fallback: str = "gemini"
    #: After a rate limit, skip the primary for this long and use the fallback.
    ai_rate_limit_cooldown_seconds: int = 120
    #: Connect+read timeout for every provider HTTP call. Intake is synchronous,
    #: so an unbounded call would hang a citizen's submission.
    ai_request_timeout_seconds: float = 20.0
    #: Largest image sent to a vision model. Larger images are downscaled.
    ai_max_image_bytes: int = 3_000_000

    # --- mock surfaces ---
    enable_mock_apis: bool = True
    gov_gateway_mode: str = "inprocess"  # inprocess | http
    mock_gov_base_url: str = "http://localhost:8000/mock/gov"
    gov_callback_secret: Optional[str] = None

    # --- social amplification policy ---
    social_min_cluster_size: int = 4
    social_priorities: str = "P1,P2"

    # --- authentication ---
    # Signs session JWTs. Separate from secret_key so capture-token signing and
    # session signing can be rotated independently.
    jwt_secret: Optional[str] = None
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720  # 12 hours
    password_min_length: int = 8
    #: bcrypt work factor. 12 is the production default; the test suite drops
    #: it to 4, because hashing five seeded accounts per test at cost 12 added
    #: two minutes to the run for no additional coverage.
    bcrypt_rounds: int = 12
    login_max_attempts: int = 8
    login_attempt_window_seconds: int = 900
    allow_citizen_self_registration: bool = True

    # --- geocoding (OpenStreetMap Nominatim, proxied through the backend) ---
    # Nominatim's usage policy requires an identifying User-Agent and at most
    # one request per second. Proxying lets us honour both and cache results,
    # which a browser calling Nominatim directly cannot do.
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "BarathSeva-AI-Prototype/0.1 (civic complaints)"
    nominatim_timeout_seconds: float = 8.0
    geocode_cache_ttl_seconds: int = 86400
    geocode_rate_limit_per_minute: int = 30

    # --- admin access ---
    # The prototype ships a shared-key gate so the mechanism exists and the
    # frontend wires through it. It is NOT real authorization: production needs
    # per-officer identity, roles and audit. Set require_admin_key=true to
    # enforce, which any non-local deployment must do.
    admin_api_key: str = "dev-admin-key"
    require_admin_key: bool = False

    @property
    def resolved_jwt_secret(self) -> str:
        """Fall back to secret_key so the prototype runs without extra config."""
        return self.jwt_secret or self.secret_key

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def social_priority_list(self) -> list[str]:
        return [p.strip() for p in self.social_priorities.split(",") if p.strip()]

    @property
    def mock_apis_enabled(self) -> bool:
        """Mock government endpoints must be impossible to expose in production,
        even if an operator leaves enable_mock_apis=true in a shared config."""
        return self.enable_mock_apis and self.environment != "production"

    def validate_runtime(self) -> list[str]:
        """Sanity-check the loaded config and describe what is wrong, if
        anything — but never raise. Intake must not fail to boot just because
        a third-party provider or a secret was misconfigured; a citizen's
        report still has to go through the stub pipeline. Callers log the
        returned warnings and continue.
        """
        warnings: list[str] = []

        provider_keys: dict[str, Optional[str]] = {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "nvidia": self.nvidia_api_key,
        }

        if self.ai_provider in provider_keys and not provider_keys[self.ai_provider]:
            warnings.append(
                f"ai_provider is '{self.ai_provider}' but no matching API key is "
                "set; requests will fail over to the stub provider."
            )

        if self.ai_rate_limit_fallback in provider_keys:
            fallback_key = provider_keys[self.ai_rate_limit_fallback]
            if not fallback_key:
                warnings.append(
                    f"ai_rate_limit_fallback is '{self.ai_rate_limit_fallback}' "
                    "but no matching API key is set; rate-limit fallback will "
                    "not be able to run."
                )

        if self.environment != "development":
            if self.secret_key == "dev-insecure-secret-change-me":
                warnings.append(
                    "secret_key is still the development default outside "
                    "development; capture tokens are forgeable."
                )
            if self.admin_api_key == "dev-admin-key":
                warnings.append(
                    "admin_api_key is still the development default outside "
                    "development; the legacy admin gate is guessable."
                )

        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
