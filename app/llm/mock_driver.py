"""The driver every test runs against. No network, no key, no cost, no flake.

Two modes. Left alone it returns a *minimal valid instance* of whatever schema it
is handed — enough for a test that only cares that the plumbing runs. Given queued
responses it returns those in order, which is how a test says "pretend the model
saw a scam" without owning a Gemini key.

It also records every call, so a test can assert that the rules engine short-circuited
an obvious scam and the vision call was never made — a cost control that is
otherwise invisible.
"""

from __future__ import annotations

import types
import typing
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.llm.base import Capabilities, LLMProvider, LLMValidationError, SchemaT, Usage

__all__ = ["MockCall", "MockProvider"]


@dataclass(frozen=True, slots=True)
class MockCall:
    """One recorded call. `payload_bytes` is a length, never the bytes themselves."""

    method: str
    prompt: str
    schema: str
    payload_bytes: int = 0
    context: dict[str, Any] = field(default_factory=dict)


def _minimal_instance(schema: type[SchemaT]) -> SchemaT:
    """Build the emptiest instance the schema will accept.

    Not a fixture generator — deliberately boring values, so a test that depends on
    the content of a mock response has to say so by queueing one.
    """
    values: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        if not info.is_required():
            continue
        values[name] = _empty_for(info.annotation)
    return schema(**values)


def _empty_for(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return None if len(args) < len(typing.get_args(annotation)) else _empty_for(args[0])
    if origin in (list, set, tuple):
        return origin()
    if origin is dict:
        return {}
    if annotation is bool:
        return False
    if annotation in (int, float):
        return 0
    if annotation is str:
        return ""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _minimal_instance(annotation)
    return None


class MockProvider(LLMProvider):
    """Canned fixtures. Declares full capabilities so no test is skipped for them."""

    name = "mock"
    model = "mock"
    capabilities = Capabilities(vision=True, native_pdf=True)

    def __init__(self, responses: list[BaseModel] | None = None, text: str = "") -> None:
        self._queue: deque[BaseModel] = deque(responses or [])
        self._text = text
        self.calls: list[MockCall] = []

    # -- programming the mock -------------------------------------------------

    def queue(self, *responses: BaseModel) -> MockProvider:
        """Queue responses, returned in order. Chainable for readable test setup."""
        self._queue.extend(responses)
        return self

    def set_text(self, text: str) -> MockProvider:
        self._text = text
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_to(self, method: str) -> list[MockCall]:
        return [c for c in self.calls if c.method == method]

    # -- the interface --------------------------------------------------------

    def _next(self, schema: type[SchemaT]) -> SchemaT:
        if not self._queue:
            return _minimal_instance(schema)
        response = self._queue.popleft()
        if not isinstance(response, schema):
            raise LLMValidationError(
                f"mock was queued a {type(response).__name__} but asked for {schema.__name__}"
            )
        return response

    def read_image(self, image: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        self.calls.append(
            MockCall("read_image", prompt, schema.__name__, payload_bytes=len(image))
        )
        return self._next(schema)

    def read_document(self, pdf: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        self.calls.append(
            MockCall("read_document", prompt, schema.__name__, payload_bytes=len(pdf))
        )
        return self._next(schema)

    def analyse(self, prompt: str, context: dict[str, Any], schema: type[SchemaT]) -> SchemaT:
        self.calls.append(MockCall("analyse", prompt, schema.__name__, context=dict(context)))
        return self._next(schema)

    def write(self, prompt: str, context: dict[str, Any], language: str) -> str:
        self.calls.append(MockCall("write", prompt, "str", context=dict(context)))
        return self._text

    def last_usage(self) -> Usage:
        """The mock spends nothing, and says so."""
        return Usage()
