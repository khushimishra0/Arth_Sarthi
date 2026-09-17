"""Routing, orchestration and the cost control that decides whether a PDF is free.

The routing tests use real PDFs built with reportlab, not mocks — the whole point
of the decision is how a real file behaves, and a mock that always agrees with us
would prove nothing.
"""

from __future__ import annotations

import io

import pytest

from app.channels.base import render_plain
from app.config import Settings
from app.features.loan.extractor import ExtractionRoute, choose_route, extract_terms
from app.features.loan.formatter import format_analysis
from app.features.loan.schema import LoanTerms
from app.features.loan.service import analyse_terms, check_document
from app.llm.base import CapabilityError, LLMUnavailable
from app.llm.mock_driver import MockProvider
from app.llm.preprocess import PdfReadError, pdf_text_yield
from app.models import repo
from app.models.db import Analysis, LlmCall
from app.models.enums import Channel, Feature
from app.utils.hashing import content_hash

SANCTION_LINES = [
    "SANCTION LETTER",
    "Borrower: A. Kumar",
    "Loan amount: Rs 1,00,000",
    "Tenure: 24 months",
    "Rate of interest: 14% per annum flat",
    "EMI: Rs 5,333 per month",
    "Processing fee: 2.5% of the loan amount plus GST at 18%",
    "Foreclosure: 4% of outstanding, after a lock-in of 6 months",
    "Late payment: 2% per month on the overdue amount",
    "Cheque bounce charge: Rs 500",
]

EXTRACTED = LoanTerms(
    lender_name="Example Finance Ltd",
    loan_type="personal",
    principal=100_000,
    tenure_months=24,
    interest_rate=14.0,
    rate_basis="flat",
    stated_emi=5333,
    processing_fee=2.5,
    processing_fee_is_pct=True,
    gst_on_fees=True,
)


def text_pdf(lines: list[str] | None = None, pages: int = 1) -> bytes:
    """A PDF with a real text layer — what a digital sanction letter looks like."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for _ in range(pages):
        for index, line in enumerate(lines or SANCTION_LINES):
            pdf.drawString(50, 790 - index * 22, line)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def scanned_pdf() -> bytes:
    """A PDF whose page is an image — what a photographed sanction letter looks like."""
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    photo = Image.new("RGB", (1200, 1600), (235, 235, 230))
    image_buffer = io.BytesIO()
    photo.save(image_buffer, "PNG")
    image_buffer.seek(0)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawImage(ImageReader(image_buffer), 0, 0, width=A4[0], height=A4[1])
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def user(session):
    return repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="77")


# ---------------------------------------------------------------------------
# Routing — the cost control
# ---------------------------------------------------------------------------


def test_a_digital_pdf_yields_plenty_of_text():
    _, pages, per_page = pdf_text_yield(text_pdf())
    assert pages == 1
    assert per_page > 200


def test_a_scanned_pdf_yields_almost_none():
    _, pages, per_page = pdf_text_yield(scanned_pdf())
    assert pages == 1
    assert per_page < 20


def test_a_digital_pdf_takes_the_free_text_route():
    _, _, per_page = pdf_text_yield(text_pdf())
    assert choose_route(per_page) is ExtractionRoute.TEXT


def test_a_scanned_pdf_needs_eyes():
    _, _, per_page = pdf_text_yield(scanned_pdf())
    assert choose_route(per_page) is ExtractionRoute.NATIVE_PDF


def test_the_threshold_sits_between_the_two_cases():
    """Not a coincidence to be preserved by luck — assert the gap is real."""
    _, _, digital = pdf_text_yield(text_pdf())
    _, _, scanned = pdf_text_yield(scanned_pdf())
    threshold = Settings().pdf_min_chars_per_page
    assert scanned < threshold < digital


def test_a_digital_pdf_never_reaches_the_vision_model():
    """Most sanction letters are digital. This is where the money is saved."""
    provider = MockProvider().queue(EXTRACTED)
    extraction = extract_terms(text_pdf(), provider)

    assert extraction.route is ExtractionRoute.TEXT
    assert provider.calls_to("read_document") == []
    assert len(provider.calls_to("analyse")) == 1


def test_a_scanned_pdf_goes_to_the_vision_model_as_a_pdf():
    provider = MockProvider().queue(EXTRACTED)
    extraction = extract_terms(scanned_pdf(), provider)

    assert extraction.route is ExtractionRoute.NATIVE_PDF
    assert len(provider.calls_to("read_document")) == 1


def test_the_documents_text_is_passed_as_untrusted_context():
    provider = MockProvider().queue(EXTRACTED)
    extract_terms(text_pdf(), provider)
    call = provider.calls_to("analyse")[0]
    assert "SANCTION LETTER" not in call.prompt
    assert "SANCTION LETTER" in call.context["document_text"]


def test_a_text_only_provider_is_refused_before_it_fails_confusingly():
    class TextOnly(MockProvider):
        from app.llm.base import Capabilities

        capabilities = Capabilities(vision=False, native_pdf=False)

    with pytest.raises(CapabilityError, match="cannot read PDFs"):
        extract_terms(scanned_pdf(), TextOnly())


def test_a_corrupt_pdf_is_rejected_before_anything_is_spent():
    provider = MockProvider()
    with pytest.raises(PdfReadError):
        extract_terms(b"not a pdf at all", provider)
    assert provider.calls == []


def test_a_multi_page_document_is_measured_per_page():
    _, pages, per_page = pdf_text_yield(text_pdf(pages=3))
    assert pages == 3
    assert per_page > 200


# ---------------------------------------------------------------------------
# The full path
# ---------------------------------------------------------------------------


async def test_a_pdf_is_analysed_and_recorded(session, user):
    provider = MockProvider().queue(EXTRACTED)
    pdf = text_pdf()

    response = await check_document(
        pdf, session=session, user=user, provider=provider, filename="sanction.pdf"
    )
    text = render_plain(response)

    assert "Loan Analysis" in text
    assert "₹1,27,992" in text
    stored = session.query(Analysis).one()
    assert stored.feature is Feature.LOAN
    assert stored.input_hash == content_hash(pdf)


async def test_the_pdf_bytes_are_never_stored(session, user):
    """A sanction letter carries a name, an address and sometimes an Aadhaar number."""
    provider = MockProvider().queue(EXTRACTED)
    pdf = text_pdf()
    await check_document(pdf, session=session, user=user, provider=provider)

    stored = session.query(Analysis).one()
    assert pdf.hex()[:40] not in str(stored.result_json)
    assert len(stored.input_hash) == 64


async def test_the_same_document_is_read_once(session, user):
    pdf = text_pdf()
    first = MockProvider().queue(EXTRACTED)
    await check_document(pdf, session=session, user=user, provider=first)

    second = MockProvider()
    response = await check_document(pdf, session=session, user=user, provider=second)

    assert second.calls == []
    assert "Loan Analysis" in render_plain(response)


async def test_a_cached_analysis_is_recomputed_not_replayed(session, user):
    """Only the terms are cached; the arithmetic is redone, so an engine fix reaches
    every past answer instead of preserving the old one."""
    pdf = text_pdf()
    await check_document(
        pdf, session=session, user=user, provider=MockProvider().queue(EXTRACTED)
    )
    stored = session.query(Analysis).one()
    assert "terms" in stored.result_json
    assert "total_repaid" not in stored.result_json

    cached = render_plain(
        await check_document(pdf, session=session, user=user, provider=MockProvider())
    )
    fresh = render_plain(format_analysis(*analyse_terms(EXTRACTED)))
    assert cached == fresh


async def test_the_call_is_written_to_the_ledger(session, user):
    provider = MockProvider().queue(EXTRACTED)
    await check_document(text_pdf(), session=session, user=user, provider=provider)

    row = session.query(LlmCall).one()
    assert row.feature is Feature.LOAN
    assert row.method == "analyse"  # the free route, so the cheap method


async def test_a_scanned_document_is_billed_as_a_document_read(session, user):
    provider = MockProvider().queue(EXTRACTED)
    await check_document(scanned_pdf(), session=session, user=user, provider=provider)
    assert session.query(LlmCall).one().method == "read_document"


async def test_an_unreadable_file_gets_advice_not_an_error(session, user):
    response = await check_document(
        b"garbage", session=session, user=user, provider=MockProvider()
    )
    assert "could not read that document" in render_plain(response)


async def test_an_outage_never_shows_a_stack_trace(session, user):
    class Broken(MockProvider):
        def analyse(self, prompt, context, schema):
            raise LLMUnavailable("gemini is down")

    response = await check_document(text_pdf(), session=session, user=user, provider=Broken())
    text = render_plain(response)
    assert "could not reach my reading service" in text
    assert "Do not sign anything in the meantime" in text


async def test_the_spend_cap_offers_the_manual_route(session, user):
    repo.record_llm_call(
        session,
        provider="gemini",
        model="m",
        method="read_document",
        tokens_in=0,
        tokens_out=0,
        cost_inr=9_999.0,
    )
    provider = MockProvider().queue(EXTRACTED)
    response = await check_document(
        text_pdf(), session=session, user=user, provider=provider, settings=Settings()
    )

    text = render_plain(response)
    assert "reading limit" in text
    assert "type the numbers" in text
    assert provider.calls == []


async def test_a_document_the_model_could_not_fill_in_still_answers(session, user):
    """The mock returns an all-null LoanTerms — the incomplete-document path."""
    response = await check_document(
        text_pdf(), session=session, user=user, provider=MockProvider()
    )
    text = render_plain(response)
    assert "not going to guess" in text
    assert "Ask the lender for, in writing" in text
