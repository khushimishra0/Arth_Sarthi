"""Provider selection, and the startup check that makes a misconfiguration loud.

`get_provider()` returns the text provider; `get_vision_provider()` returns the one
used for images and scanned PDFs. They are usually the same object. The split exists
because `analyse()` and `write()` are plain text reasoning and paying vision prices
for them is waste.

`verify_configuration()` is the part that matters. It runs at startup and refuses to
boot a configuration that cannot do the job — a text-only model selected for vision
is an error message in a log at 9am, not a failed analysis in front of a user at the
demo. It never makes a network call, so it costs nothing and works offline.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import ProviderName, Settings, get_settings
from app.llm.base import CapabilityError, LLMProvider
from app.llm.mock_driver import MockProvider

__all__ = [
    "build_provider",
    "get_provider",
    "get_vision_provider",
    "reset_providers",
    "verify_configuration",
]

log = logging.getLogger(__name__)


def build_provider(name: ProviderName, settings: Settings) -> LLMProvider:
    """Construct one driver. Missing credentials fail here, with the fix in the message."""
    if name == "mock":
        return MockProvider()

    if name == "gemini":
        if settings.gemini_api_key is None:
            raise CapabilityError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. "
                "Get a key at aistudio.google.com and put it in .env"
            )
        from app.llm.gemini_driver import GeminiProvider

        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
        )

    if name == "deepseek":
        if settings.deepseek_api_key is None:
            raise CapabilityError(
                "LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set. "
                "Get a key at platform.deepseek.com and put it in .env"
            )
        from app.llm.deepseek_driver import DeepSeekProvider

        return DeepSeekProvider(
            api_key=settings.deepseek_api_key.get_secret_value(), model=settings.deepseek_model
        )

    raise CapabilityError(f"unknown provider {name!r}")


@lru_cache
def get_provider() -> LLMProvider:
    """The text provider. Cached — a driver holds a connection pool, not state."""
    settings = get_settings()
    return build_provider(settings.llm_provider, settings)


@lru_cache
def get_vision_provider() -> LLMProvider:
    """The provider used for images and scanned PDFs."""
    settings = get_settings()
    if settings.vision_provider == settings.llm_provider:
        return get_provider()
    return build_provider(settings.vision_provider, settings)


def reset_providers() -> None:
    """Drop the cached drivers. For tests and for a config reload."""
    get_provider.cache_clear()
    get_vision_provider.cache_clear()


def verify_configuration(settings: Settings | None = None) -> None:
    """Fail at startup if the configured providers cannot do what the features need.

    Called from the FastAPI lifespan and the bot's polling entry point. Raises
    `CapabilityError` with the env change that fixes it — never a stack trace about
    a missing method three layers down.
    """
    settings = settings or get_settings()

    text = build_provider(settings.llm_provider, settings)
    vision = (
        text
        if settings.vision_provider == settings.llm_provider
        else build_provider(settings.vision_provider, settings)
    )

    # Scam detection reads screenshots and loan analysis reads scanned PDFs. A
    # provider that can do neither cannot run this product's two headline features.
    vision.require_vision()

    if not vision.capabilities.native_pdf:
        log.warning(
            "%s has no native PDF input; scanned documents will need page rendering",
            vision.name,
        )

    log.info(
        "llm configured: text=%s vision=%s%s",
        text.name,
        vision.name,
        " (mock — no API calls will be made)" if text.name == "mock" else "",
    )
