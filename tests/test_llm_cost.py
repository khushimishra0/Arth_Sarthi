"""Cost and privacy controls at the ingest boundary.

The EXIF test is the one that matters most and looks least important. A photo of a
phone screen carries the GPS coordinates of the room it was taken in — usually
somebody's home. If that assertion ever fails, uploads are leaking location data to
a third party, and nothing else in this file matters.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from PIL import Image
from PIL.ExifTags import Base as ExifTag

from app.config import Settings
from app.llm.base import SpendCapReached, Usage
from app.llm.mock_driver import MockProvider
from app.llm.preprocess import ImagePrepError, PdfReadError, pdf_text_yield, prepare_image
from app.llm.spend import (
    DailyCapReached,
    check_caps,
    estimate_cost_inr,
    record_call,
    remaining_budget_inr,
)
from app.models import repo
from app.models.db import LlmCall
from app.models.enums import Channel, Feature


def _photo(width: int, height: int, *, with_exif: bool = True) -> bytes:
    image = Image.new("RGB", (width, height), (120, 60, 200))
    buffer = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[ExifTag.Make.value] = "TestPhone"
        exif[ExifTag.Model.value] = "Model X"
        exif[ExifTag.Orientation.value] = 1
        # A GPS block is what actually matters; any tag proves the strip works.
        image.save(buffer, "JPEG", exif=exif.tobytes())
    else:
        image.save(buffer, "JPEG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------


def test_a_large_photo_is_downscaled_to_the_configured_edge():
    prepared = prepare_image(_photo(4000, 3000), 1568)
    assert max(Image.open(io.BytesIO(prepared)).size) == 1568


def test_the_aspect_ratio_survives_the_downscale():
    """A squashed screenshot is an unreadable screenshot."""
    prepared = prepare_image(_photo(4000, 2000), 1568)
    width, height = Image.open(io.BytesIO(prepared)).size
    assert width / height == pytest.approx(2.0, abs=0.01)


def test_downscaling_actually_saves_money():
    original = _photo(4000, 3000)
    assert len(prepare_image(original, 1568)) < len(original) / 2


def test_metadata_is_stripped_from_every_upload():
    """GPS in a photo of a phone screen is somebody's home address."""
    prepared = prepare_image(_photo(2000, 1500), 1568)
    assert Image.open(io.BytesIO(prepared)).info.get("exif") is None


def test_a_small_image_is_still_re_encoded_so_its_metadata_goes_too():
    """The tempting optimisation — pass small images through — would keep the GPS tag."""
    prepared = prepare_image(_photo(400, 300), 1568)
    reopened = Image.open(io.BytesIO(prepared))
    assert reopened.info.get("exif") is None
    assert reopened.size == (400, 300)  # not upscaled either


def test_a_rotated_photo_is_uprighted_before_the_tag_is_discarded():
    """Orientation applied, then dropped. Otherwise the model reads it sideways."""
    image = Image.new("RGB", (400, 200), (10, 10, 10))
    exif = Image.Exif()
    exif[ExifTag.Orientation.value] = 6  # rotate 90°
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif.tobytes())

    prepared = prepare_image(buffer.getvalue(), 1568)
    assert Image.open(io.BytesIO(prepared)).size == (200, 400)


def test_a_non_image_is_rejected_with_something_a_user_can_act_on():
    with pytest.raises(ImagePrepError, match="could not read this as an image"):
        prepare_image(b"this is not a photograph", 1568)


def test_a_truncated_image_does_not_crash_the_handler():
    truncated = _photo(1000, 800)[:120]
    with pytest.raises(ImagePrepError):
        prepare_image(truncated, 1568)


# ---------------------------------------------------------------------------
# PDF text-before-vision
# ---------------------------------------------------------------------------


def test_a_corrupt_pdf_is_rejected_cleanly():
    with pytest.raises(PdfReadError, match="could not read this as a PDF"):
        pdf_text_yield(b"%PDF-1.4 but not really")


def test_an_empty_pdf_yields_zero_rather_than_dividing_by_zero():
    """Zero pages must not become a ZeroDivisionError in front of a user."""
    from pypdf import PdfWriter  # type: ignore[import-not-found]

    pytest.importorskip("pypdf")
    writer = PdfWriter()
    buffer = io.BytesIO()
    writer.write(buffer)
    text, pages, per_page = pdf_text_yield(buffer.getvalue())
    assert pages == 0 and per_page == 0.0 and text == ""


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def test_the_mock_provider_costs_nothing():
    assert estimate_cost_inr(MockProvider(), Usage(10_000, 5_000), Settings()) == 0.0


def test_cost_is_computed_from_published_per_token_rates():
    from app.llm.gemini_driver import GeminiProvider

    settings = Settings(gemini_input_inr_per_mtok=25.0, gemini_output_inr_per_mtok=200.0)
    provider = GeminiProvider(api_key="k", client=object())
    # 1M in + 1M out at those rates.
    cost = estimate_cost_inr(provider, Usage(1_000_000, 1_000_000), settings)
    assert cost == pytest.approx(225.0)


def test_a_typical_screenshot_call_costs_paise_not_rupees():
    """Sanity on the order of magnitude — the ₹2,000 cap should buy thousands of checks."""
    from app.llm.gemini_driver import GeminiProvider

    provider = GeminiProvider(api_key="k", client=object())
    cost = estimate_cost_inr(provider, Usage(1_500, 300), Settings())
    assert 0 < cost < 1.0


# ---------------------------------------------------------------------------
# The ledger and the caps
# ---------------------------------------------------------------------------


def test_a_call_is_recorded_with_its_tokens_and_cost(session):
    provider = MockProvider()
    cost = record_call(session, provider, method="read_image", feature=Feature.SCAM)

    row = session.query(LlmCall).one()
    assert row.provider == "mock" and row.model == "mock"
    assert row.method == "read_image" and row.feature is Feature.SCAM
    assert row.cost_inr == cost == 0.0


def test_the_ledger_is_not_linked_to_a_user(session):
    """Deleting a user must not reset the spend cap. The row has no user_id at all."""
    assert "user_id" not in {c.name for c in LlmCall.__table__.columns}


def test_spend_this_month_sums_only_this_month(session):
    """Fixed dates, not offsets from today — run on the 2nd, "3 days ago" is last month."""
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    for cost, when in (
        (10.0, datetime(2026, 8, 1, 0, 1, tzinfo=UTC)),  # first minute of the month
        (5.0, datetime(2026, 8, 19, tzinfo=UTC)),  # yesterday
        (99.0, datetime(2026, 7, 31, 23, 59, tzinfo=UTC)),  # last minute of last month
    ):
        call = repo.record_llm_call(
            session,
            provider="gemini",
            model="gemini-3.6-flash",
            method="analyse",
            tokens_in=1,
            tokens_out=1,
            cost_inr=cost,
        )
        call.created_at = when
    session.commit()

    assert repo.spend_this_month(session, now=now) == pytest.approx(15.0)


def test_no_spend_is_zero_not_none(session):
    assert repo.spend_this_month(session) == 0.0


def test_caps_allow_a_call_when_there_is_headroom(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="1")
    check_caps(session, user, Settings())  # must not raise


def test_the_monthly_cap_stops_spending_before_the_call(session):
    repo.record_llm_call(
        session,
        provider="gemini",
        model="m",
        method="analyse",
        tokens_in=0,
        tokens_out=0,
        cost_inr=2_500.0,
    )
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="1")
    with pytest.raises(SpendCapReached, match="monthly cap"):
        check_caps(session, user, Settings(monthly_spend_cap_inr=2000))


def test_the_daily_cap_limits_one_user_without_affecting_others(session):
    heavy = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="heavy")
    light = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="light")
    settings = Settings(daily_analyses_per_user=3)

    for i in range(3):
        repo.record_analysis(
            session, heavy, feature=Feature.SCAM, input_hash=f"{i:064d}", result={}
        )

    with pytest.raises(DailyCapReached, match="3 of 3"):
        check_caps(session, heavy, settings)
    check_caps(session, light, settings)  # unaffected


def test_the_monthly_cap_is_reported_before_the_personal_one(session):
    """Telling one user "you've used your 20" when the project is out of money
    sends them back tomorrow to hit the same wall."""
    repo.record_llm_call(
        session,
        provider="gemini",
        model="m",
        method="analyse",
        tokens_in=0,
        tokens_out=0,
        cost_inr=5_000.0,
    )
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="1")
    for i in range(30):
        repo.record_analysis(session, user, feature=Feature.SCAM, input_hash=f"{i:064d}", result={})

    with pytest.raises(SpendCapReached) as raised:
        check_caps(session, user, Settings())
    assert not isinstance(raised.value, DailyCapReached)


def test_remaining_budget_never_goes_negative(session):
    repo.record_llm_call(
        session,
        provider="gemini",
        model="m",
        method="analyse",
        tokens_in=0,
        tokens_out=0,
        cost_inr=9_999.0,
    )
    assert remaining_budget_inr(session, Settings(monthly_spend_cap_inr=2000)) == 0.0
