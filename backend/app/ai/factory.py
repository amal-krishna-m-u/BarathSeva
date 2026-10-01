"""Provider selection with graceful degradation.

If a model provider is configured but unusable — missing SDK, missing key, or a
failing call — the platform falls back to the deterministic provider rather
than dropping the complaint. Intake must never depend on a third party being
up; classification quality degrades instead.
"""

from __future__ import annotations

import logging
import threading

from app.ai.base import AIProvider, InferenceRequest, InferenceResult
from app.ai.stub import StubProvider
from app.config import settings

logger = logging.getLogger(__name__)


def _dashboard_config() -> tuple[str, dict[str, object]]:
    """Read the active provider and its credentials from the database.

    Opens its own short-lived session rather than taking one, because
    get_provider() is called from deep inside agents that are mid-transaction,
    and a settings lookup must not join or disturb that transaction. Any
    failure here degrades to the environment -- a credentials table that cannot
    be read must never take down intake.
    """
    try:
        from app.ai.credentials import CONFIGURABLE_PROVIDERS, active_provider, resolve
        from app.db import SessionLocal

        with SessionLocal() as db:
            choice = (active_provider(db) or "stub").strip().lower()
            resolved = {p: resolve(db, p) for p in CONFIGURABLE_PROVIDERS}
            return choice, resolved
    except ImportError as exc:
        # The credentials stack is not installed at all. That is a deliberate
        # deployment shape -- the aiKart sandbox image ships without the
        # database layer on purpose -- not a fault, so it must not log like
        # one on every single inference.
        logger.debug("credentials stack unavailable (%s); using settings", exc)
        return (settings.ai_provider or "stub").strip().lower(), {}
    except Exception as exc:  # pragma: no cover - defensive
        # Installed but unreachable: a database that should be there and is
        # not. Worth a warning, because somebody needs to look at it.
        logger.warning("could not read provider credentials (%s); using settings", exc)
        return (settings.ai_provider or "stub").strip().lower(), {}


def _build_provider() -> AIProvider:
    choice, resolved = _dashboard_config()

    def _key(provider: str, fallback):
        got = resolved.get(provider)
        return got.api_key if got and got.api_key else fallback

    def _model(provider: str, fallback):
        got = resolved.get(provider)
        return got.model if got and got.model else fallback

    if choice in {"stub", "", "none", "deterministic"}:
        return StubProvider()

    if choice == "openai":
        api_key = _key("openai", settings.openai_api_key)
        if not api_key:
            logger.warning("ai_provider=openai but no API key is configured; using stub")
            return StubProvider()
        try:
            from app.ai.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key, _model("openai", settings.openai_model))
        except Exception as exc:
            logger.warning("OpenAI provider unavailable (%s); using stub", exc)
            return StubProvider()

    if choice == "gemini":
        api_key = _key("gemini", settings.gemini_api_key)
        if not api_key:
            logger.warning("ai_provider=gemini but no API key is configured; using stub")
            return StubProvider()
        try:
            from app.ai.gemini_provider import GeminiProvider

            return GeminiProvider(api_key, _model("gemini", settings.gemini_model))
        except Exception as exc:
            logger.warning("Gemini provider unavailable (%s); using stub", exc)
            return StubProvider()

    if choice in {"nvidia", "kimi"}:
        api_key = _key("nvidia", settings.nvidia_api_key)
        if not api_key:
            logger.warning("ai_provider=%s but no API key is configured; using stub", choice)
            return StubProvider()
        try:
            from app.ai.nvidia_provider import NvidiaProvider

            return NvidiaProvider(
                api_key,
                _model("nvidia", settings.nvidia_model),
                settings.nvidia_base_url,
                settings.ai_request_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("NVIDIA provider unavailable (%s); using stub", exc)
            return StubProvider()

    logger.warning("Unknown ai_provider=%r; using stub", choice)
    return StubProvider()


#: Module-level cached singleton, built lazily on first ``get_provider()``
#: call. A bare zero-argument ``@lru_cache`` cannot be reset selectively or
#: hold mutable state alongside the provider (the cooldown ladder in a later
#: task needs exactly that), so the cache is explicit instead.
_provider_cache: AIProvider | None = None

#: Guards ``_provider_cache``. FastAPI runs sync routes on a threadpool, so
#: concurrent first calls are reachable in production, not just a theoretical
#: race. Double-construction of a stateless provider is harmless, but Task 8
#: hangs mutable cooldown state off this exact singleton — a lost race there
#: means lost cooldown writes and readers seeing inconsistent state. Do not
#: remove this lock when that state is added; it is the reason it exists.
_provider_lock = threading.Lock()


def get_provider() -> AIProvider:
    global _provider_cache
    if _provider_cache is None:
        with _provider_lock:
            if _provider_cache is None:  # re-check: another thread may have built it
                _provider_cache = _build_provider()
                logger.info(
                    "AI provider: %s (%s, is_ai=%s)",
                    _provider_cache.name,
                    _provider_cache.model,
                    _provider_cache.is_ai,
                )
    return _provider_cache


def reset_provider_cache() -> None:
    """Drop the cached provider so the next ``get_provider()`` rebuilds it.

    Tests use this to swap providers between cases (stub <-> fakes) without
    process restarts; a later task uses it as the reset point for the
    fallback chain's cooldown state.
    """
    global _provider_cache
    with _provider_lock:
        _provider_cache = None


_FALLBACK = StubProvider()


def infer(request: InferenceRequest) -> InferenceResult:
    """Run inference, falling back to deterministic rules on provider failure."""
    provider = get_provider()
    result = provider.infer(request)
    if result.ok:
        return result

    logger.warning(
        "provider %s failed on task %s (%s); falling back to stub",
        provider.name,
        request.task,
        result.error,
    )
    fallback = _FALLBACK.infer(request)
    fallback.error = f"fell_back_from_{provider.name}: {result.error}"
    # Additive only: carry forward *why* the primary failed. No branching on
    # it here — the stub is still used unconditionally, exactly as before.
    fallback.error_kind = result.error_kind
    return fallback
