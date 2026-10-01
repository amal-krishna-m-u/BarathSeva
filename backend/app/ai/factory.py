"""Provider selection with graceful degradation.

If a model provider is configured but unusable — missing SDK, missing key, or a
failing call — the platform falls back to the deterministic provider rather
than dropping the complaint. Intake must never depend on a third party being
up; classification quality degrades instead.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.ai.base import AIProvider, InferenceRequest, InferenceResult
from app.ai.stub import StubProvider
from app.config import settings

logger = logging.getLogger(__name__)


def _build_provider() -> AIProvider:
    choice = (settings.ai_provider or "stub").strip().lower()

    if choice in {"stub", "", "none", "deterministic"}:
        return StubProvider()

    if choice == "openai":
        if not settings.openai_api_key:
            logger.warning("ai_provider=openai but BARATHSEVA_OPENAI_API_KEY is unset; using stub")
            return StubProvider()
        try:
            from app.ai.openai_provider import OpenAIProvider

            return OpenAIProvider(settings.openai_api_key, settings.openai_model)
        except Exception as exc:
            logger.warning("OpenAI provider unavailable (%s); using stub", exc)
            return StubProvider()

    if choice == "gemini":
        if not settings.gemini_api_key:
            logger.warning("ai_provider=gemini but BARATHSEVA_GEMINI_API_KEY is unset; using stub")
            return StubProvider()
        try:
            from app.ai.gemini_provider import GeminiProvider

            return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
        except Exception as exc:
            logger.warning("Gemini provider unavailable (%s); using stub", exc)
            return StubProvider()

    logger.warning("Unknown ai_provider=%r; using stub", choice)
    return StubProvider()


@lru_cache
def get_provider() -> AIProvider:
    provider = _build_provider()
    logger.info("AI provider: %s (%s, is_ai=%s)", provider.name, provider.model, provider.is_ai)
    return provider


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
    return fallback
