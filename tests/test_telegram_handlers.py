"""End-to-end handler tests against a fake bot.

Gate 2 is "message the live bot on a phone and get the welcome back". This file is
the offline half of that: a real `telegram.Update` goes into the real handler, the
real database is touched, and what comes out the far end is inspected — with a
stub standing in for the network. What it cannot prove is that BotFather issued a
working token. Everything up to that boundary is covered here.
"""

from __future__ import annotations

import base64
import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Document, Message, MessageOriginUser, PhotoSize, Update
from telegram import User as TelegramUser

from app.channels import copy
from app.channels.telegram_bot import (
    language_command,
    on_callback,
    on_document,
    on_photo,
    on_text,
    profile_command,
    start,
)
from app.models.enums import Channel, Language
from app.models.session import init_db, session_scope

TELEGRAM_USER_ID = 4242


@pytest.fixture(autouse=True)
def _database() -> None:
    init_db()
    from app.models.db import delete_user

    with session_scope() as session:
        delete_user(session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID))


@pytest.fixture
def bot() -> AsyncMock:
    """Everything Telegram would do over the wire, recorded instead of sent."""
    stub = AsyncMock()
    stub.send_message = AsyncMock()
    stub.send_photo = AsyncMock()
    stub.send_chat_action = AsyncMock()
    return stub


@pytest.fixture
def context(bot: AsyncMock) -> SimpleNamespace:
    """A stand-in for PTB's context. Downloads return a real 1-pixel PNG.

    Real bytes, not a sentinel: the photo path runs Pillow over whatever it is
    given, and a handler test that never exercises the downscale would miss the
    day it starts throwing.
    """
    telegram_file = AsyncMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(_ONE_PIXEL_PNG))
    bot.get_file = AsyncMock(return_value=telegram_file)
    return SimpleNamespace(bot=bot)


@pytest.fixture
def pdf_context(bot: AsyncMock) -> SimpleNamespace:
    """Downloads return a real one-page PDF with a text layer, so the router runs."""
    telegram_file = AsyncMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(_text_pdf()))
    bot.get_file = AsyncMock(return_value=telegram_file)
    return SimpleNamespace(bot=bot)


def _text_pdf() -> bytes:
    """A minimal PDF carrying enough text that `pdf_text_yield` routes it as digital."""
    from reportlab.lib.pagesizes import A4  # type: ignore[import-not-found]
    from reportlab.pdfgen import canvas  # type: ignore[import-not-found]

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for index, line in enumerate(
        [
            "SANCTION LETTER",
            "Loan amount: Rs 1,00,000",
            "Tenure: 24 months",
            "Rate of interest: 14% per annum flat",
            "EMI: Rs 5,333 per month",
            "Processing fee: 2.5% of loan amount plus GST",
        ]
    ):
        pdf.drawString(60, 780 - index * 24, line)
    pdf.save()
    return buffer.getvalue()


# A valid 1x1 PNG. Small enough to inline, real enough for Pillow to open.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _update(
    bot: AsyncMock,
    *,
    text: str | None = None,
    photo: bool = False,
    document: Document | None = None,
    forwarded: bool = False,
) -> Update:
    chat = Chat(id=TELEGRAM_USER_ID, type=Chat.PRIVATE)
    sender = TelegramUser(id=TELEGRAM_USER_ID, first_name="Test", is_bot=False)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=sender,
        text=text,
        document=document,
        photo=(PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600),)
        if photo
        else None,
        forward_origin=MessageOriginUser(
            date=datetime.now(UTC),
            sender_user=TelegramUser(id=777, first_name="Uncle", is_bot=False),
        )
        if forwarded
        else None,
    )
    for obj in (chat, sender, message):
        obj.set_bot(bot)
    update = Update(update_id=1, message=message)
    update.set_bot(bot)
    return update


def _sent_text(bot: AsyncMock) -> str:
    return "\n".join(call.kwargs["text"] for call in bot.send_message.await_args_list)


def _sent_markup(bot: AsyncMock):
    return bot.send_message.await_args_list[-1].kwargs["reply_markup"]


# ---------------------------------------------------------------------------
# Gate 2: /start returns the welcome and four buttons
# ---------------------------------------------------------------------------


async def test_start_replies_with_the_trust_paragraph_and_four_buttons(bot):
    await start(_update(bot, text="/start"), None)

    assert bot.send_message.await_count == 1
    text = _sent_text(bot)
    for promise in copy.trust_lines():
        assert promise in text

    markup = _sent_markup(bot)
    tokens = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert tokens == [action for action, _ in copy.MAIN_ACTIONS]


async def test_start_creates_the_user_record_with_no_form(bot):
    from app.models import repo

    await start(_update(bot, text="/start"), None)
    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID)
        )
        assert user.id is not None
        assert user.language is Language.ENGLISH


async def test_the_reply_is_sent_as_html_so_the_bold_survives(bot):
    await start(_update(bot, text="/start"), None)
    assert bot.send_message.await_args_list[0].kwargs["parse_mode"] == "HTML"


# ---------------------------------------------------------------------------
# Zero-command paths, through the real handlers
# ---------------------------------------------------------------------------


async def test_a_photo_is_answered_with_a_real_scam_assessment(bot, context):
    """No command, no explanation — a photo arrives and gets analysed."""
    await on_photo(_update(bot, photo=True), context)
    text = _sent_text(bot)
    assert "%" in text
    assert "What to do now" in text
    bot.get_file.assert_awaited()  # the bytes were actually fetched and read


async def test_a_forwarded_text_is_scored_without_a_vision_call(bot, context):
    """Text needs no eyes, so the free path runs and nothing is downloaded."""
    update = _update(bot, text="Congratulations! You won. Pay Rs 500 fee.", forwarded=True)
    await on_text(update, context)
    assert "What to do now" in _sent_text(bot)
    bot.get_file.assert_not_awaited()


async def test_a_pdf_is_answered_with_a_real_loan_analysis(bot, pdf_context):
    """No command needed — a PDF arrives and gets read, routed and costed."""
    pdf = Document(
        file_id="d1",
        file_unique_id="du1",
        file_name="sanction.pdf",
        mime_type="application/pdf",
        file_size=120_000,
    )
    await on_document(_update(bot, document=pdf), pdf_context)
    text = _sent_text(bot)
    assert "Loan Analysis" in text
    assert "sanction.pdf" in text
    bot.get_file.assert_awaited()


async def test_an_oversized_file_gets_a_next_action_not_an_error(bot):
    """§8: never a naked error. The user is told what to send instead."""
    huge = Document(
        file_id="d2",
        file_unique_id="du2",
        file_name="scan.pdf",
        mime_type="application/pdf",
        file_size=50 * 1024 * 1024,
    )
    await on_document(_update(bot, document=huge), None)
    text = _sent_text(bot)
    assert "MB" in text
    assert "photo of the important page" in text
    assert _sent_markup(bot) is not None


async def test_an_unreadable_file_type_explains_what_to_send(bot):
    docx = Document(
        file_id="d3",
        file_unique_id="du3",
        file_name="terms.docx",
        mime_type="application/msword",
        file_size=5_000,
    )
    await on_document(_update(bot, document=docx), None)
    assert "PDF" in _sent_text(bot)
    assert _sent_markup(bot) is not None


async def test_a_forwarded_message_is_treated_as_a_scam_check(bot, context):
    update = _update(bot, text="Dear customer your KYC expires today", forwarded=True)
    await on_text(update, context)
    text = _sent_text(bot).lower()
    assert "%" in text and "what to do now" in text


async def test_budget_numbers_run_the_coach_rather_than_offering_it(bot):
    """Phase 5 replaced the offer with the analysis. One line in, chart and goal out."""
    await on_text(_update(bot, text="income 25000 rent 8000 food 4000"), None)

    text = _sent_text(bot)
    assert "Your month" in text
    assert "₹25,000" in text
    assert "savings goal" in text.lower()
    assert bot.send_photo.await_count == 1  # the pie chart


async def test_an_ambiguous_budget_line_falls_back_to_the_guided_flow(bot):
    """§7: ambiguous parsing must not be acted on. Numbers with no label are the case."""
    await on_text(_update(bot, text="income 25000 8000 4000"), None)

    text = _sent_text(bot)
    assert "could not tell what these numbers were for" in text
    assert bot.send_photo.await_count == 0  # nothing was drawn from a half-read month


async def test_unrecognised_chat_gets_buttons_and_never_the_word_invalid(bot):
    await on_text(_update(bot, text="hello ji"), None)
    text = _sent_text(bot).lower()
    assert "invalid" not in text and "error" not in text
    assert _sent_markup(bot) is not None


async def test_govt_schemes_button_starts_the_first_question_immediately(bot):
    update = Update(update_id=6, callback_query=_callback(bot, "schemes"))
    update.set_bot(bot)

    await on_callback(update, None)

    assert "Step 1 of 7" in _sent_text(bot)
    actions = [b.callback_data for row in _sent_markup(bot).inline_keyboard for b in row]
    assert "scheme:gender:female" in actions


async def test_okay_resumes_an_unfinished_scheme_questionnaire(bot):
    tap = Update(update_id=7, callback_query=_callback(bot, "schemes"))
    tap.set_bot(bot)
    await on_callback(tap, None)

    bot.reset_mock()
    await on_text(_update(bot, text="Okay"), None)

    text = _sent_text(bot)
    assert "Step 1 of 7" in text
    assert "did not quite catch" not in text.lower()


async def test_the_step_counter_counts_every_step_exactly_once(bot):
    """It used to say "Question 3 of 6" for two different questions in a row.

    A progress counter is the one thing telling somebody how much longer this takes,
    so one that repeats a number and then skips one is worse than none at all. The
    questions are a list now and the number is the index, which is why this holds.
    """
    seen = []
    actions = (
        "schemes",
        "scheme:gender:female",
        "scheme:age:26_35",
        "scheme:state:bihar",
        "scheme:area:rural",
        "scheme:income:10k_25k",
        "scheme:need:education",
    )
    for update_id, action in enumerate(actions, start=60):
        bot.reset_mock()
        update = Update(update_id=update_id, callback_query=_callback(bot, action))
        update.set_bot(bot)
        await on_callback(update, None)
        first_line = _sent_text(bot).splitlines()[0]
        seen.append(first_line)

    numbers = [line.split("Step ", 1)[1].split(" of ")[0].strip() for line in seen]
    assert numbers == ["1", "2", "3", "4", "5", "6", "7"], seen
    assert all("of 7" in line for line in seen), seen


async def test_back_undoes_one_answer_instead_of_the_whole_questionnaire(bot):
    """A mistap on question 1 used to mean redoing all seven."""
    for update_id, action in enumerate(("schemes", "scheme:gender:female"), start=80):
        update = Update(update_id=update_id, callback_query=_callback(bot, action))
        update.set_bot(bot)
        await on_callback(update, None)

    # Now on question 2 (Age). Back must return to question 1, not to the start.
    bot.reset_mock()
    back = Update(update_id=90, callback_query=_callback(bot, "scheme:back"))
    back.set_bot(bot)
    await on_callback(back, None)

    text = _sent_text(bot)
    assert "Step 1 of 7" in text
    assert "did not quite catch" not in text.lower()

    # And the answer really was cleared, so the next tap re-answers it.
    from app.models import repo

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID)
        )
        assert repo.get_or_create_profile(session, user).gender is None


async def test_every_question_after_the_first_offers_a_way_back(bot):
    for update_id, action in enumerate(("schemes", "scheme:gender:female"), start=100):
        bot.reset_mock()
        update = Update(update_id=update_id, callback_query=_callback(bot, action))
        update.set_bot(bot)
        await on_callback(update, None)

    actions = [b.callback_data for row in _sent_markup(bot).inline_keyboard for b in row]
    assert "scheme:back" in actions


async def test_scheme_questionnaire_saves_every_answer_and_finishes_honestly(bot):
    actions = (
        "schemes",
        "scheme:gender:female",
        "scheme:age:26_35",
        "scheme:state:bihar",
        "scheme:area:rural",
        "scheme:income:10k_25k",
        "scheme:need:education",
        "scheme:category:obc",
    )
    for update_id, action in enumerate(actions, start=10):
        bot.reset_mock()
        update = Update(update_id=update_id, callback_query=_callback(bot, action))
        update.set_bot(bot)
        await on_callback(update, None)

    # Seven taps in, real matches out — this is §6's worked example: a woman in a
    # Bihar village on ₹10-25k a month who needs help with education.
    text = _sent_text(bot)
    assert "Your answers are saved" in text
    assert "You may qualify for" in text
    assert "PM Vidyalaxmi" in text
    assert "Details checked" in text  # §6: the verification date is always shown

    apply_labels = [b.text for row in _sent_markup(bot).inline_keyboard for b in row]
    assert any("Apply" in label for label in apply_labels)

    from app.models import repo

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID)
        )
        profile = user.profile
        assert profile is not None
        assert profile.gender.value == "female"
        assert profile.age_band.value == "26_35"
        assert profile.state == "bihar"
        assert profile.area.value == "rural"
        assert profile.income_band.value == "10k_25k"
        assert profile.needs == ["education"]
        assert profile.category.value == "obc"


# ---------------------------------------------------------------------------
# Profile, deletion and language
# ---------------------------------------------------------------------------


async def test_profile_shows_what_is_stored_and_offers_deletion(bot):
    await profile_command(_update(bot, text="/profile"), None)
    text = _sent_text(bot)
    assert "No name" in text and "No phone number" in text
    actions = [b.callback_data for row in _sent_markup(bot).inline_keyboard for b in row]
    assert "profile_delete_confirm" in actions


async def test_deleting_asks_for_confirmation_first(bot):
    update = Update(update_id=2, callback_query=_callback(bot, "profile_delete_confirm"))
    update.set_bot(bot)
    await on_callback(update, None)
    text = _sent_text(bot)
    assert "cannot be undone" in text
    actions = [b.callback_data for row in _sent_markup(bot).inline_keyboard for b in row]
    assert actions == ["profile_delete_do", "start"]


async def test_confirming_the_delete_removes_every_row(bot):
    from app.models import repo
    from app.models.db import Analysis, User
    from app.models.enums import Feature

    await start(_update(bot, text="/start"), None)
    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID)
        )
        repo.record_analysis(
            session, user, feature=Feature.SCAM, input_hash="9" * 64, result={"score": 1}
        )

    bot.reset_mock()
    update = Update(update_id=3, callback_query=_callback(bot, "profile_delete_do"))
    update.set_bot(bot)
    await on_callback(update, None)

    assert "Deleted" in _sent_text(bot)
    with session_scope() as session:
        remaining = (
            session.query(User)
            .filter(User.channel_user_id == str(TELEGRAM_USER_ID))
            .one_or_none()
        )
        assert remaining is None
        assert session.query(Analysis).count() == 0


async def test_choosing_hindi_saves_the_preference_and_says_so_honestly(bot):
    from app.models import repo

    await language_command(_update(bot, text="/language"), None)
    actions = [b.callback_data for row in _sent_markup(bot).inline_keyboard for b in row]
    assert actions == ["lang_en", "lang_hi"]

    bot.reset_mock()
    update = Update(update_id=4, callback_query=_callback(bot, "lang_hi"))
    update.set_bot(bot)
    await on_callback(update, None)

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(TELEGRAM_USER_ID)
        )
        assert user.language is Language.HINDI


# ---------------------------------------------------------------------------
# Callback taps route like commands
# ---------------------------------------------------------------------------


def _callback(bot: AsyncMock, data: str):
    from telegram import CallbackQuery

    chat = Chat(id=TELEGRAM_USER_ID, type=Chat.PRIVATE)
    sender = TelegramUser(id=TELEGRAM_USER_ID, first_name="Test", is_bot=False)
    message = Message(message_id=9, date=datetime.now(UTC), chat=chat, from_user=sender)
    query = CallbackQuery(
        id="q1", from_user=sender, chat_instance="ci", data=data, message=message
    )
    for obj in (chat, sender, message, query):
        obj.set_bot(bot)
    return query


@pytest.mark.parametrize("action", [a for a, _ in copy.MAIN_ACTIONS])
async def test_every_main_button_tap_produces_a_reply(bot, action):
    update = Update(update_id=5, callback_query=_callback(bot, action))
    update.set_bot(bot)
    await on_callback(update, None)
    assert bot.send_message.await_count >= 1
    assert _sent_text(bot).strip()
