"""Gemini driver — vision, native PDF, Hindi, and a free tier.

Chosen as the primary provider because it is the only one that does all four things
the product needs without a pre-processing step: it reads a photographed WhatsApp
screenshot, it takes a scanned loan PDF *as a PDF*, it handles Devanagari and
Hinglish, and it can be demonstrated without a bill.

Model ID (Phase 0.5, confirmed against Google's docs at wiring time, August 2026):
`gemini-3.6-flash` is the current stable balanced model. Overridable with
`GEMINI_MODEL` — the ID will move again, and a demo should never be blocked on a
code change to chase it.

Structured output is enforced by the API, not by parsing prose: every call passes
`response_schema`, so the model is constrained to the shape we asked for. When it
still comes back wrong, the driver retries **once** with the validation error
appended, then fails honestly. Half-parsed output never reaches a user.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.llm.base import (
    Capabilities,
    LLMProvider,
    LLMUnavailable,
    LLMValidationError,
    SchemaT,
    Usage,
)
from app.llm.prompting import language_instruction, strip_fences, with_context

__all__ = ["DEFAULT_MODEL", "GeminiProvider"]

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"

# Deterministic by default. This product's job is to give the same answer about the
# same screenshot twice; creative variation is a defect here, not a feature.
DEFAULT_TEMPERATURE = 0.0

_JSON = "application/json"

_RETRY_PREAMBLE = (
    "Your previous response did not match the required schema and was rejected.\n"
    "Error: {error}\n"
    "Return JSON matching the schema exactly. Do not add commentary or code fences.\n\n"
)


class GeminiProvider(LLMProvider):
    """Google Gemini via `google-genai`. The only file that imports that SDK."""

    name = "gemini"
    capabilities = Capabilities(vision=True, native_pdf=True)

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, client: Any = None) -> None:
        # `client` is injectable so the tests can exercise every path — including
        # the retry and both failure modes — without a key and without a network.
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self.model = model
        self._usage = Usage()

    # -- the interface --------------------------------------------------------

    def read_image(self, image: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        return self._structured([self._part(image, "image/jpeg"), prompt], schema)

    def read_document(self, pdf: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        return self._structured([self._part(pdf, "application/pdf"), prompt], schema)

    def analyse(self, prompt: str, context: dict[str, Any], schema: type[SchemaT]) -> SchemaT:
        return self._structured([with_context(prompt, context)], schema)

    def write(self, prompt: str, context: dict[str, Any], language: str) -> str:
        instruction = f"{with_context(prompt, context)}\n\n{language_instruction(language)}"
        response = self._generate([instruction], response_schema=None)
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise LLMValidationError("gemini returned an empty completion")
        return text

    def last_usage(self) -> Usage:
        return self._usage

    # -- internals ------------------------------------------------------------

    def _part(self, data: bytes, mime_type: str) -> Any:
        from google.genai import types

        return types.Part.from_bytes(data=data, mime_type=mime_type)

    def _generate(self, contents: list[Any], *, response_schema: Any) -> Any:
        from google.genai import errors, types

        config = types.GenerateContentConfig(temperature=DEFAULT_TEMPERATURE)
        if response_schema is not None:
            config.response_mime_type = _JSON
            config.response_schema = response_schema

        try:
            response = self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except errors.APIError as exc:
            # Rate limits, quota exhaustion, 5xx — all "come back in a moment",
            # none of them the user's fault, and none of them worth a stack trace.
            raise LLMUnavailable(f"gemini call failed: {exc}") from exc

        self._usage = _usage_of(response)
        return response

    def _structured(self, contents: list[Any], schema: type[SchemaT]) -> SchemaT:
        response = self._generate(contents, response_schema=schema)
        raw = getattr(response, "text", None) or ""
        try:
            return schema.model_validate_json(strip_fences(raw))
        except ValidationError as exc:
            # Bound to an outer name on purpose: Python unbinds `exc` at the end of
            # the except block, and the retry prompt needs the error text.
            first_error = exc
            log.warning("gemini response failed %s validation; retrying once", schema.__name__)

        retry_contents = [_RETRY_PREAMBLE.format(error=first_error), *contents]
        response = self._generate(retry_contents, response_schema=schema)
        raw = getattr(response, "text", None) or ""
        try:
            return schema.model_validate_json(strip_fences(raw))
        except ValidationError as second_error:
            raise LLMValidationError(
                f"gemini could not produce valid {schema.__name__} after a retry: {second_error}"
            ) from second_error


def _usage_of(response: Any) -> Usage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return Usage()
    return Usage(
        tokens_in=int(getattr(metadata, "prompt_token_count", 0) or 0),
        tokens_out=int(getattr(metadata, "candidates_token_count", 0) or 0),
    )
