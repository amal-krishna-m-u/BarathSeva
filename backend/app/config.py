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
    ai_provider: str = "stub"  # stub | openai | gemini
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"

    # --- social amplification policy ---
    social_min_cluster_size: int = 4
    social_priorities: str = "P1,P2"

    # --- messaging ---
    telegram_bot_token: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
