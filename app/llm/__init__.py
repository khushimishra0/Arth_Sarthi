"""The AI adapter layer. One interface, several drivers, no SDK above this line."""

from app.llm.base import (
    Capabilities,
    CapabilityError,
    LLMError,
    LLMProvider,
    LLMUnavailable,
    LLMValidationError,
    SpendCapReached,
    Usage,
)
from app.llm.factory import (
    build_provider,
    get_provider,
    get_vision_provider,
    reset_providers,
    verify_configuration,
)
from app.llm.mock_driver import MockProvider

__all__ = [
    "Capabilities",
    "CapabilityError",
    "LLMError",
    "LLMProvider",
    "LLMUnavailable",
    "LLMValidationError",
    "MockProvider",
    "SpendCapReached",
    "Usage",
    "build_provider",
    "get_provider",
    "get_vision_provider",
    "reset_providers",
    "verify_configuration",
]
