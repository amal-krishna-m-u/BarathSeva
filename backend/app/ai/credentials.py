"""Where a provider's API key and model actually come from.

Precedence, highest first:

  1. a ``provider_credentials`` row set from the admin dashboard
  2. the environment (``BARATHSEVA_GEMINI_API_KEY`` and friends)

That order, and not the reverse, because the dashboard is the thing an operator
can reach. A deployment that already configures everything through .env keeps
working with no row present; the moment someone saves a key in the dashboard,
that is a deliberate act and it wins.

Every read is best-effort: if the database is unreachable or a stored key
cannot be decrypted (a rotated secret key, say), this falls back to the
environment rather than raising. Inference degrading to the stub is survivable;
the intake path failing on every complaint because a settings table is unhappy
is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.core.crypto import decrypt_secret

logger = logging.getLogger(__name__)

#: The providers the dashboard can configure. Mirrors app.ai.factory's registry;
#: "stub" is deliberately absent because it takes no credentials.
CONFIGURABLE_PROVIDERS = ("gemini", "nvidia", "openai")

#: Fallback model per provider when neither the row nor the environment pins one.
DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "nvidia": "moonshotai/kimi-k3",
    "openai": "gpt-4o-mini",
}


@dataclass(frozen=True)
class ResolvedCredential:
    provider: str
    api_key: Optional[str]
    model: str
    #: "database" | "environment" | "none" -- surfaced in the admin UI so an
    #: operator can see WHY a provider is configured the way it is.
    source: str


def _env_credential(provider: str) -> tuple[Optional[str], Optional[str]]:
    key = getattr(settings, f"{provider}_api_key", None)
    model = getattr(settings, f"{provider}_model", None)
    return key, model


def _row_for(db, provider: str):
    from app.models import ProviderCredential

    return (
        db.query(ProviderCredential).filter(ProviderCredential.provider == provider).one_or_none()
    )


def resolve(db, provider: str) -> ResolvedCredential:
    """Resolve one provider's key and model."""
    env_key, env_model = _env_credential(provider)
    key, model, source = env_key, env_model, "environment" if env_key else "none"

    try:
        row = _row_for(db, provider)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("provider_credentials unreadable (%s); using environment", exc)
        row = None

    if row is not None:
        if row.api_key_encrypted:
            stored = decrypt_secret(row.api_key_encrypted)
            if stored:
                key, source = stored, "database"
            else:
                logger.warning(
                    "stored key for %s could not be decrypted (secret key rotated?); "
                    "falling back to the environment",
                    provider,
                )
        if row.model:
            model = row.model

    return ResolvedCredential(
        provider=provider,
        api_key=key,
        model=model or DEFAULT_MODELS.get(provider, ""),
        source=source,
    )


def active_provider(db) -> str:
    """Which provider should serve inference right now.

    An active dashboard row wins over ``settings.ai_provider`` for the same
    reason the key does: it is the switch an operator can actually reach.
    """
    try:
        from app.models import ProviderCredential

        row = (
            db.query(ProviderCredential)
            .filter(ProviderCredential.is_active.is_(True))
            .first()
        )
        if row is not None:
            return row.provider
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not read the active provider (%s); using settings", exc)
    return settings.ai_provider
