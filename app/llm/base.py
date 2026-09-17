"""The LLMProvider interface — §3 of the plan, plus one addition.

Every feature depends on this and nothing else. No feature file imports an AI SDK,
which is what makes "we have not picked a provider" a survivable position: swapping
Gemini for DeepSeek is a config change, and running the whole test suite against a
canned mock costs nothing and works on a plane.

The addition is `Capabilities`. A text-only driver declares `vision=False`, and the
factory refuses that configuration at *startup* — not halfway through analysing a
grandmother's screenshot, in front of her.

Why these methods are synchronous
---------------------------------
The ABC is sync, exactly as §3 specifies. Drivers stay simple and testable, with no
async plumbing in the part of the system most likely to be rewritten. The Telegram
handlers are async, so the service layer calls into a provider with
`asyncio.to_thread` — one line, at the one boundary that actually cares about the
event loop. Putting `async` on every driver method to avoid that line would spread
the concern across every provider we ever write.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, NamedTuple, TypeVar

from pydantic import BaseModel

__all__ = [
    "Capabilities",
    "CapabilityError",
    "LLMError",
    "LLMProvider",
    "LLMUnavailable",
    "LLMValidationError",
    "SpendCapReached",
    "Usage",
]

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class Capabilities(NamedTuple):
    """What a driver can physically do. Checked at startup, not at call time."""

    vision: bool
    native_pdf: bool


class Usage(NamedTuple):
    """Token counts from the last call, for the spend ledger.

    Zeros are honest: the mock driver spends nothing, and a provider that does not
    report usage should record zero rather than a fabricated estimate.
    """

    tokens_in: int = 0
    tokens_out: int = 0


class LLMError(RuntimeError):
    """Base for everything that can go wrong below the feature layer.

    Every subclass carries a message written for a *developer*. The user-facing
    sentence is chosen by the feature, because only the feature knows what the user
    was trying to do and what they should try instead.
    """


class LLMUnavailable(LLMError):
    """The provider could not be reached, or refused: network, 5xx, rate limit."""


class LLMValidationError(LLMError):
    """The model's output did not match the schema, twice.

    The driver retries once with the validation error appended to the prompt. If
    that also fails, the feature says "I could not read this, send a clearer photo"
    — never half-parsed output.
    """


class CapabilityError(LLMError):
    """A provider was asked for something it cannot do (images on a text-only model)."""


class SpendCapReached(LLMError):
    """A configured cap would be exceeded. Raised *before* the call, not after."""


class LLMProvider(ABC):
    """One interface, many drivers. §3, verbatim, plus `capabilities` and `usage`."""

    name: str
    capabilities: Capabilities
    #: The specific model behind this driver, for the spend ledger.
    model: str = ""

    @abstractmethod
    def read_image(self, image: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """Vision: WhatsApp screenshot → structured extraction."""

    @abstractmethod
    def read_document(self, pdf: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """Document: loan PDF → structured field extraction."""

    @abstractmethod
    def analyse(self, prompt: str, context: dict[str, Any], schema: type[SchemaT]) -> SchemaT:
        """Text reasoning: judgement, explanation, advice."""

    @abstractmethod
    def write(self, prompt: str, context: dict[str, Any], language: str) -> str:
        """Plain-language generation: the advice paragraph the user reads."""

    def last_usage(self) -> Usage:
        """Tokens consumed by the most recent call. Default: nothing was spent."""
        return Usage()

    def require_vision(self) -> None:
        if not self.capabilities.vision:
            raise CapabilityError(
                f"{self.name} cannot read images. Set LLM_VISION_PROVIDER to a "
                f"provider that can, e.g. LLM_VISION_PROVIDER=gemini"
            )

    def require_native_pdf(self) -> None:
        if not self.capabilities.native_pdf:
            raise CapabilityError(
                f"{self.name} cannot read PDFs directly. Extract the text first, or "
                f"set LLM_VISION_PROVIDER=gemini"
            )
