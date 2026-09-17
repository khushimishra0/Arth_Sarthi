"""Routing a PDF to the cheapest thing that can actually read it. §5 step 1.

A digital sanction letter has a text layer. pdfplumber pulls it out for nothing, and
the text then goes to a *text* model — which is cheaper than a vision model and, on
text, at least as accurate. Many Indian sanction letters are scans of a printout,
which have no text layer at all; those must go to the vision model as a PDF.

`pdf_text_yield` decides which. A digital page yields hundreds of characters; a
scanned page yields single digits, usually stray marks the extractor mistook for
letters. The threshold sits far from both.

No PyMuPDF page-rendering step here. Gemini takes the PDF natively, which removes an
entire dependency and an entire class of "the render looked fine but the model saw
nothing" bug. That step only comes back if we move to a provider without native PDF
— which is exactly what `Capabilities.native_pdf` exists to catch at startup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings, get_settings
from app.features.loan.prompts import EXTRACTION_PROMPT, text_extraction_prompt
from app.features.loan.schema import LoanTerms
from app.llm.base import LLMProvider
from app.llm.preprocess import pdf_text_yield

__all__ = ["ExtractionRoute", "LoanExtraction", "choose_route", "extract_terms"]

log = logging.getLogger(__name__)


class ExtractionRoute(StrEnum):
    TEXT = "text"
    NATIVE_PDF = "native_pdf"


@dataclass(frozen=True, slots=True)
class LoanExtraction:
    """The terms, plus how they were obtained — which the cost ledger wants to know."""

    terms: LoanTerms
    route: ExtractionRoute
    pages: int
    chars_per_page: float

    @property
    def method(self) -> str:
        return "analyse" if self.route is ExtractionRoute.TEXT else "read_document"


def choose_route(
    chars_per_page: float, settings: Settings | None = None
) -> ExtractionRoute:
    """Above the threshold the PDF carries its own text; below it, it is a picture."""
    settings = settings or get_settings()
    return (
        ExtractionRoute.TEXT
        if chars_per_page >= settings.pdf_min_chars_per_page
        else ExtractionRoute.NATIVE_PDF
    )


def extract_terms(
    pdf: bytes,
    provider: LLMProvider,
    settings: Settings | None = None,
) -> LoanExtraction:
    """Read a loan PDF into `LoanTerms`, by the cheapest route that works.

    Raises `PdfReadError` if the file is not a readable PDF, and whatever the
    provider raises if the model cannot produce a valid schema after its retry.
    """
    settings = settings or get_settings()
    text, pages, chars_per_page = pdf_text_yield(pdf)
    route = choose_route(chars_per_page, settings)

    log.info(
        "loan pdf: %d pages, %.0f chars/page → %s", pages, chars_per_page, route.value
    )

    if route is ExtractionRoute.TEXT:
        prompt, context = text_extraction_prompt(text)
        terms = provider.analyse(prompt, context, LoanTerms)
    else:
        provider.require_native_pdf()
        terms = provider.read_document(pdf, EXTRACTION_PROMPT, LoanTerms)

    return LoanExtraction(
        terms=terms, route=route, pages=pages, chars_per_page=chars_per_page
    )
