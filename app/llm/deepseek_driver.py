"""DeepSeek driver — text only, and honest about it.

Optional secondary. Its whole reason to exist is that `analyse()` and `write()` are
plain text reasoning, and paying vision-model prices for them is waste. It declares
`vision=False` and `native_pdf=False`; the factory turns that into a startup error
if someone points `LLM_PROVIDER=deepseek` at a product whose first feature is
reading screenshots.

OpenAI-compatible HTTP over plain `httpx` — no second SDK for one endpoint.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.llm.base import (
    Capabilities,
    CapabilityError,
    LLMProvider,
    LLMUnavailable,
    LLMValidationError,
    SchemaT,
    Usage,
)
from app.llm.prompting import language_instruction, strip_fences, with_context

__all__ = ["DEFAULT_MODEL", "DeepSeekProvider"]

log = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
REQUEST_TIMEOUT_SECONDS = 60.0


class DeepSeekProvider(LLMProvider):
    name = "deepseek"
    capabilities = Capabilities(vision=False, native_pdf=False)

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._usage = Usage()

    def read_image(self, image: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        raise CapabilityError(
            "deepseek cannot read images. Set LLM_VISION_PROVIDER=gemini to keep "
            "DeepSeek for text and let Gemini do the seeing."
        )

    def read_document(self, pdf: bytes, prompt: str, schema: type[SchemaT]) -> SchemaT:
        raise CapabilityError(
            "deepseek cannot read PDFs. Extract the text first, or set "
            "LLM_VISION_PROVIDER=gemini."
        )

    def analyse(self, prompt: str, context: dict[str, Any], schema: type[SchemaT]) -> SchemaT:
        instruction = (
            f"{with_context(prompt, context)}\n\n"
            f"Respond with JSON matching this schema:\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
        )
        raw = self._chat(instruction, json_mode=True)
        try:
            return schema.model_validate_json(strip_fences(raw))
        except ValidationError as exc:
            first_error = exc
            log.warning("deepseek response failed %s validation; retrying once", schema.__name__)

        retry = (
            f"Your previous response was rejected: {first_error}\n"
            f"Return JSON matching the schema exactly.\n\n{instruction}"
        )
        raw = self._chat(retry, json_mode=True)
        try:
            return schema.model_validate_json(strip_fences(raw))
        except ValidationError as exc:
            raise LLMValidationError(
                f"deepseek could not produce valid {schema.__name__} after a retry: {exc}"
            ) from exc

    def write(self, prompt: str, context: dict[str, Any], language: str) -> str:
        instruction = f"{with_context(prompt, context)}\n\n{language_instruction(language)}"
        text = self._chat(instruction, json_mode=False).strip()
        if not text:
            raise LLMValidationError("deepseek returned an empty completion")
        return text

    def last_usage(self) -> Usage:
        return self._usage

    def _chat(self, content: str, *, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"deepseek call failed: {exc}") from exc

        usage = body.get("usage") or {}
        self._usage = Usage(
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
        )
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"deepseek returned an unexpected body: {body}") from exc
