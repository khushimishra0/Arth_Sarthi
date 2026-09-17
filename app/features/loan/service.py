"""Orchestration: PDF in, six-section analysis out. Same shape as scam detection.

Cache → caps → route → extract → compute → detect → advise → format. The routing
step is the cost control that matters here: a digital sanction letter never reaches
a vision model, and most sanction letters are digital.

Never raises. Every failure below this line has a user-facing sentence and a next
action, because a borrower standing in a lender's office with a document in their
hand needs an answer, not a stack trace.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.config import Settings, get_settings
from app.features.loan.advisor import Advice, advise
from app.features.loan.computed import ComputedLoan, compute
from app.features.loan.extractor import LoanExtraction, extract_terms
from app.features.loan.formatter import DISCLAIMER, format_analysis, format_unreadable
from app.features.loan.hidden import Finding, detect
from app.features.loan.schema import LoanTerms
from app.llm import spend
from app.llm.base import (
    CapabilityError,
    LLMProvider,
    LLMUnavailable,
    LLMValidationError,
    SpendCapReached,
)
from app.llm.preprocess import PdfReadError
from app.models import repo
from app.models.db import User
from app.models.enums import Feature
from app.utils.hashing import content_hash

__all__ = ["analyse_terms", "analyse_pdf", "check_document"]

log = logging.getLogger(__name__)


def analyse_terms(
    terms: LoanTerms, settings: Settings | None = None
) -> tuple[ComputedLoan, list[Finding], Advice]:
    """Everything after extraction. No AI, no I/O — the auditable half of the feature."""
    computed = compute(terms, settings)
    findings = detect(computed)
    return computed, findings, advise(computed, findings)


def analyse_pdf(
    pdf: bytes,
    provider: LLMProvider,
    session: Session | None = None,
    settings: Settings | None = None,
) -> tuple[LoanExtraction, ComputedLoan, list[Finding], Advice]:
    """Route, extract, then compute. Raises `PdfReadError` on an unreadable file."""
    settings = settings or get_settings()
    extraction = extract_terms(pdf, provider, settings)
    if session is not None:
        spend.record_call(session, provider, method=extraction.method, feature=Feature.LOAN)

    computed, findings, advice = analyse_terms(extraction.terms, settings)
    return extraction, computed, findings, advice


async def check_document(
    pdf: bytes,
    *,
    session: Session,
    user: User,
    provider: LLMProvider,
    filename: str = "",
    settings: Settings | None = None,
) -> OutboundMessage:
    """The whole feature, as a channel calls it. Never raises."""
    settings = settings or get_settings()
    digest = content_hash(pdf)

    cached = repo.cached_analysis(session, feature=Feature.LOAN, input_hash=digest)
    if cached is not None:
        log.info("loan cache hit for %s", digest[:12])
        return _from_stored(cached.result_json, filename)

    try:
        spend.check_caps(session, user, settings)
    except SpendCapReached as exc:
        return _cap_reached(exc)

    try:
        extraction, computed, findings, advice = await asyncio.to_thread(
            analyse_pdf, pdf, provider, session, settings
        )
    except PdfReadError:
        return format_unreadable()
    except CapabilityError as exc:
        log.error("loan analysis misconfigured: %s", exc)
        return _misconfigured()
    except (LLMUnavailable, LLMValidationError) as exc:
        log.warning("loan analysis failed: %s", exc)
        return _temporarily_unavailable()

    repo.record_analysis(
        session,
        user,
        feature=Feature.LOAN,
        input_hash=digest,
        result=_to_stored(extraction, computed, findings, advice),
    )
    return format_analysis(computed, findings, advice, filename)


# ---------------------------------------------------------------------------
# Cache serialisation
#
# The extracted terms are stored — they are the analysis. The PDF is not, and a
# sanction letter contains a name, an address, an account number and sometimes an
# Aadhaar number. The row is deleted after 90 days.
# ---------------------------------------------------------------------------


def _to_stored(
    extraction: LoanExtraction,
    computed: ComputedLoan,
    findings: list[Finding],
    advice: Advice,
) -> dict:
    return {
        "terms": extraction.terms.model_dump(mode="json"),
        "route": extraction.route.value,
        "verdict": advice.verdict.value,
        "true_apr_pct": computed.true_apr_pct,
        "finding_ids": [finding.id for finding in findings],
    }


def _from_stored(stored: dict, filename: str) -> OutboundMessage:
    """Re-derive rather than re-render.

    Only the extracted terms are cached; the arithmetic is recomputed from them.
    It is free, it cannot drift from the current engine, and it means a fix to
    `finance.py` improves every cached answer instead of preserving the old one.
    """
    terms = LoanTerms.model_validate(stored["terms"])
    computed, findings, advice = analyse_terms(terms)
    return format_analysis(computed, findings, advice, filename)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def _retry_keyboard() -> Keyboard:
    return Keyboard.of([Button(label="📄 Try again", action="loan_analysis")])


def _cap_reached(exc: SpendCapReached) -> OutboundMessage:
    log.warning("loan analysis refused: %s", exc)
    return OutboundMessage.of(
        Block.para(
            "I have hit my reading limit for now, so I cannot open documents at the moment. "
            "This is my limit, not anything about your loan."
        ),
        Block.para(
            "If it is urgent, type the numbers and I will still do the maths: amount, "
            "tenure in months, interest rate, whether it is flat or reducing, EMI, and any fees."
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )


def _temporarily_unavailable() -> OutboundMessage:
    return OutboundMessage.of(
        Block.para(
            "I could not reach my reading service just now. Nothing you sent was saved, and "
            "this will usually work if you try again in a minute."
        ),
        Block.para("Do not sign anything in the meantime just because a lender is waiting."),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )


def _misconfigured() -> OutboundMessage:
    return OutboundMessage.of(
        Block.para(
            "I am not set up to read documents right now — that is a problem on my side and "
            "someone has been told."
        ),
        Block.para(
            "Type the numbers and I will still do the maths: amount, tenure, rate, EMI, fees."
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )
