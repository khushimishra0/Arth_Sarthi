"""Telegram rendering and dispatch tests. No token, no network — pure string work.

The escaping tests matter more than they look. Scam detection quotes the suspicious
message back to the user, and scam messages are full of `<`, `&` and stray angle
brackets. One unescaped character is a `Can't parse entities` error where the
analysis should have been.
"""

from __future__ import annotations

import pytest

from app.channels import copy
from app.channels.base import (
    AttachmentKind,
    Block,
    Button,
    InboundMessage,
    Intent,
    Keyboard,
    OutboundMessage,
)
from app.channels.telegram_bot import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    _attachment_kind,
    _dispatch,
    build_application,
    render_html,
    render_keyboard,
    webhook_secret_token,
)
from app.config import Settings
from app.models.enums import Channel, Language


def _inbound(intent: Intent, **kwargs) -> InboundMessage:
    return InboundMessage(user_id="1", channel=Channel.TELEGRAM, intent=intent, **kwargs)


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_angle_brackets_in_quoted_content_are_escaped():
    hostile = "Click <b>here</b> & win ₹10,00,000 <script>alert(1)</script>"
    (chunk,) = render_html(OutboundMessage.of(Block.para(hostile)))
    assert "<script>" not in chunk
    assert "&lt;script&gt;" in chunk
    assert "&amp; win" in chunk


def test_headings_are_the_only_bold_and_they_wrap_escaped_text():
    (chunk,) = render_html(OutboundMessage.of(Block.heading("Risk <High>")))
    assert chunk == "<b>Risk &lt;High&gt;</b>"


def test_key_values_put_the_number_on_its_own_line():
    (chunk,) = render_html(OutboundMessage.of(Block.key_values([("Real cost", "₹1,27,992")])))
    assert chunk == "<b>Real cost</b>\n₹1,27,992"


def test_bullets_render_with_a_marker():
    (chunk,) = render_html(OutboundMessage.of(Block.bullets(["one", "two"])))
    assert chunk == "• one\n• two"


def test_an_empty_message_still_returns_one_chunk():
    assert render_html(OutboundMessage.of()) == [""]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_a_long_response_is_split_into_sendable_chunks():
    message = OutboundMessage.of(*[Block.para("x" * 400) for _ in range(20)])
    chunks = render_html(message)
    assert len(chunks) > 1
    assert all(len(c) <= TELEGRAM_MAX_MESSAGE_CHARS for c in chunks)


def test_a_single_oversized_block_is_still_cut_below_the_hard_limit():
    """One giant block cannot be split on a boundary, so it is cut anyway.

    Telegram rejects the whole message otherwise — a truncated answer beats none.
    """
    monster = "\n".join(["y" * 80] * 200)
    chunks = render_html(OutboundMessage.of(Block.para(monster)))
    assert all(len(c) <= TELEGRAM_MAX_MESSAGE_CHARS for c in chunks)
    assert sum(len(c) for c in chunks) >= len(monster) - len(chunks)


def test_the_welcome_fits_in_one_message():
    """§8: no message longer than roughly two phone screens. /start is the first
    impression and must not arrive as three separate notifications."""
    assert len(render_html(copy.welcome())) == 1


def test_the_three_promises_are_in_the_first_chunk_of_start():
    first = render_html(copy.welcome())[0]
    for promise in copy.trust_lines():
        assert promise.replace("&", "&amp;") in first


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


def test_the_four_buttons_render_two_per_row():
    markup = render_keyboard(copy.main_keyboard())
    assert markup is not None
    assert [len(row) for row in markup.inline_keyboard] == [2, 2]


def test_callback_data_is_the_action_token():
    markup = render_keyboard(copy.main_keyboard())
    tokens = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert tokens == [action for action, _ in copy.MAIN_ACTIONS]


def test_a_url_button_carries_a_url_and_no_callback():
    markup = render_keyboard(
        Keyboard.of([Button(label="Report at 1930", url="https://cybercrime.gov.in")])
    )
    button = markup.inline_keyboard[0][0]
    assert button.url == "https://cybercrime.gov.in"
    assert button.callback_data is None


def test_no_keyboard_renders_as_none():
    assert render_keyboard(None) is None
    assert render_keyboard(Keyboard()) is None


# ---------------------------------------------------------------------------
# Attachment typing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mime", "filename", "expected"),
    [
        ("application/pdf", "sanction.pdf", AttachmentKind.PDF),
        (None, "sanction.pdf", AttachmentKind.PDF),
        ("image/jpeg", "photo.jpg", AttachmentKind.IMAGE),
        (None, "screenshot.PNG", AttachmentKind.IMAGE),
        ("application/octet-stream", "loan.pdf", AttachmentKind.PDF),
        ("application/msword", "terms.docx", AttachmentKind.OTHER),
        (None, None, AttachmentKind.OTHER),
    ],
)
def test_file_type_is_read_from_the_mime_type_or_the_name(mime, filename, expected):
    """WhatsApp-forwarded documents often arrive with a useless mime type."""
    assert _attachment_kind(mime, filename) is expected


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_start_dispatches_to_the_welcome():
    assert _dispatch(_inbound(Intent.START)) == copy.welcome()


def test_help_dispatches_to_the_help_screen():
    assert _dispatch(_inbound(Intent.HELP)) == copy.help_message()


@pytest.mark.parametrize(
    "intent",
    [Intent.SCAM_CHECK, Intent.LOAN_ANALYSIS, Intent.SCHEMES, Intent.BUDGET],
)
def test_every_feature_intent_gets_its_own_prompt(intent):
    message = _dispatch(_inbound(intent))
    assert message == copy.prompt_for(intent.value)
    assert message != copy.fallback()


def test_an_unknown_intent_gets_buttons_not_an_error():
    assert _dispatch(_inbound(Intent.UNKNOWN)) == copy.fallback()


def test_dispatch_respects_the_users_language():
    """Hindi is untranslated until Phase 7, but the language must reach the copy layer."""
    assert _dispatch(_inbound(Intent.START, language=Language.HINDI)) == copy.welcome(
        Language.HINDI
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_building_the_bot_without_a_token_says_what_to_do_about_it():
    with pytest.raises(RuntimeError, match="BotFather"):
        build_application(Settings(telegram_bot_token=None))


def test_the_webhook_secret_is_derived_from_the_token_and_leaks_nothing():
    token = "123456:AAFakeTokenForTestsOnly"
    settings = Settings(telegram_bot_token=token)
    secret = webhook_secret_token(settings)

    assert len(secret) == 48
    assert token not in secret
    assert "AAFakeTokenForTestsOnly" not in secret
    assert secret == webhook_secret_token(settings)  # stable across calls
    assert secret != webhook_secret_token(Settings(telegram_bot_token=token + "x"))


def test_every_command_in_the_help_screen_has_a_handler():
    """A /help that lists a command the bot ignores is a dead end — §8 forbids those."""
    settings = Settings(telegram_bot_token="123456:AAFakeTokenForTestsOnly")
    application = build_application(settings)
    registered = {
        command
        for group in application.handlers.values()
        for handler in group
        for command in getattr(handler, "commands", set()) or set()
    }
    listed = {
        pair[0].lstrip("/")
        for block in copy.help_message().blocks
        for pair in block.pairs
        if pair[0].startswith("/")
    }
    assert listed <= registered, f"listed but not handled: {listed - registered}"
    assert {"start", "help"} <= registered


def test_the_bot_token_is_never_logged():
    """Telegram puts the token in the URL path and httpx logs URLs at INFO.

    Found live: startup printed the full credential into the console and the log
    file. Anyone holding it can read every message sent to the bot and reply as it.
    """
    import logging

    from app.channels.telegram_bot import _silence_token_leaking_loggers

    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.INFO)

    _silence_token_leaking_loggers()

    for noisy in ("httpx", "httpcore"):
        assert logging.getLogger(noisy).level >= logging.WARNING, (
            f"{noisy} logs request URLs, which contain the bot token"
        )
