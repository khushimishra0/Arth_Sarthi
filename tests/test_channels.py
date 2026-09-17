"""Channel-neutral contract tests.

Intent routing is the whole of §8's zero-command design, and it is a pure function
— so it is tested here with no bot token, no network and no Telegram import. If
this file passes, the routing works; the handler layer only has to hand it the
right arguments.
"""

from __future__ import annotations

import pytest

from app.channels import copy
from app.channels.base import (
    Attachment,
    AttachmentKind,
    Block,
    BlockKind,
    Button,
    InboundMessage,
    Intent,
    Keyboard,
    OutboundMessage,
    classify,
    looks_like_budget_text,
    render_plain,
)
from app.models.enums import Channel, Language
from app.utils.hashing import content_hash

PNG = Attachment(kind=AttachmentKind.IMAGE, content=b"\x89PNG fake", filename="scam.png")
PDF = Attachment(kind=AttachmentKind.PDF, content=b"%PDF-1.4 fake", filename="sanction.pdf")
DOCX = Attachment(kind=AttachmentKind.OTHER, content=b"PK fake", filename="terms.docx")


# ---------------------------------------------------------------------------
# Zero-command routing
# ---------------------------------------------------------------------------


def test_a_photo_goes_to_scam_detection_with_no_command():
    assert classify(files=(PNG,)) is Intent.SCAM_CHECK


def test_a_pdf_goes_to_loan_analysis_with_no_command():
    assert classify(files=(PDF,)) is Intent.LOAN_ANALYSIS


def test_a_forwarded_message_is_a_scam_check():
    """Forwarding is exactly the behaviour the feature exists to interrupt."""
    assert classify(text="Congratulations! You have won", forwarded=True) is Intent.SCAM_CHECK


def test_an_attachment_beats_the_forwarded_flag():
    """A loan agent forwards you the sanction letter. That is a loan, not a scam check."""
    assert classify(files=(PDF,), forwarded=True) is Intent.LOAN_ANALYSIS


def test_an_explicit_command_beats_everything():
    assert classify(command="/budget", files=(PDF,), forwarded=True) is Intent.BUDGET


def test_a_command_with_a_bot_suffix_still_routes():
    """Group chats deliver `/scam_check@ArthaSathiBot`."""
    assert classify(command="/scam_check@ArthaSathiBot") is Intent.SCAM_CHECK


def test_an_unknown_command_is_not_an_error():
    assert classify(command="/wallet") is Intent.UNKNOWN


def test_plain_conversation_falls_through_to_the_buttons():
    assert classify(text="hello") is Intent.UNKNOWN
    assert classify() is Intent.UNKNOWN


def test_an_unsupported_file_type_is_not_routed_to_a_reader():
    assert classify(files=(DOCX,)) is Intent.UNKNOWN


# ---------------------------------------------------------------------------
# Budget text sniffing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "income 25000 rent 8000 food 4000 travel 3000",
        "salary 25k rent 8k",
        "my kiraya is 8000 and khana 4000",
        "₹25,000 income, expenses 20000",
        "emi 4801 per month",
    ],
)
def test_numbers_next_to_money_words_offer_the_budget_coach(text):
    assert looks_like_budget_text(text) is True
    assert classify(text=text) is Intent.BUDGET


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "my loan was rejected",  # a money word, no number
        "call me on 9 pm",  # a number, no money word
        "thanks bhai",
        "what can you do",
    ],
)
def test_prose_is_never_mistaken_for_a_budget(text):
    assert looks_like_budget_text(text) is False


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_an_attachments_bytes_never_appear_in_its_repr():
    """A traceback must not spill somebody's sanction letter into a log file."""
    secret = b"ACCOUNT 1234567890 AADHAAR 9999 8888 7777"
    attachment = Attachment(kind=AttachmentKind.PDF, content=secret, filename="loan.pdf")
    rendered = repr(attachment)
    assert "1234567890" not in rendered
    assert "AADHAAR" not in rendered
    assert "loan.pdf" in rendered


def test_the_digest_is_the_only_durable_trace_of_a_file():
    assert PDF.digest == content_hash(PDF.content)
    assert len(PDF.digest) == 64


def test_identical_files_share_a_digest_so_the_cache_can_hit():
    twin = Attachment(kind=AttachmentKind.PDF, content=PDF.content, filename="other-name.pdf")
    assert twin.digest == PDF.digest


def test_inbound_reports_what_it_carries():
    message = InboundMessage(
        user_id="1",
        channel=Channel.TELEGRAM,
        intent=Intent.SCAM_CHECK,
        files=(PNG, PDF),
    )
    assert message.has_image and message.has_pdf


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


def test_a_button_needs_exactly_one_destination():
    with pytest.raises(ValueError, match="exactly one"):
        Button(label="broken")
    with pytest.raises(ValueError, match="exactly one"):
        Button(label="broken", action="a", url="https://example.com")


def test_render_plain_covers_every_block_kind():
    """Any new block kind must be handled here before a channel can rely on it."""
    message = OutboundMessage.of(
        Block.heading("Heading"),
        Block.para("Body"),
        Block.bullets(["one", "two"]),
        Block.steps(["first", "second"]),
        Block.key_values([("EMI", "₹4,801")]),
        Block.divider(),
        Block.disclaimer("Not advice."),
    )
    text = render_plain(message)
    for kind in BlockKind:
        assert kind in {b.kind for b in message.blocks}
    assert "Heading" in text
    assert "• one" in text
    assert "1. first\n2. second" in text  # ordered, and numbered by the renderer
    assert "EMI\n₹4,801" in text  # the number on its own line
    assert "Not advice." in text


def test_features_never_emit_channel_markup():
    """The point of the layer: no HTML, no MarkdownV2, no chat ids in a feature's output."""
    for message in (copy.welcome(), copy.help_message(), copy.fallback()):
        text = render_plain(message)
        assert "<" not in text and "*" not in text and "_" not in text


# ---------------------------------------------------------------------------
# Copy — the trust paragraph is a product requirement, so it is a test
# ---------------------------------------------------------------------------


def test_start_states_the_three_promises_before_anything_else():
    """§8: the first three lines say what we never ask for and that we sell nothing."""
    message = copy.welcome()
    blocks = list(message.blocks)
    assert blocks[0].kind is BlockKind.HEADING
    assert blocks[1].kind is BlockKind.BULLETS
    assert blocks[1].items == copy.trust_lines()

    text = render_plain(message)
    first_capability = text.index("Check if a message")
    for promise in copy.trust_lines():
        assert text.index(promise) < first_capability


def test_start_names_every_credential_we_will_never_ask_for():
    promise = copy.trust_lines()[0].lower()
    for credential in ("password", "pin", "otp", "cvv", "card number"):
        assert credential in promise


def test_start_says_we_sell_nothing():
    assert "sell nothing" in copy.trust_lines()[1].lower()


def test_start_says_uploads_are_deleted():
    third = copy.trust_lines()[2].lower()
    assert "deleted" in third and "never stored" in third


def test_start_offers_four_buttons():
    keyboard = copy.welcome().keyboard
    assert keyboard is not None
    assert len(keyboard.buttons) == 4
    assert [b.action for b in keyboard.buttons] == [a for a, _ in copy.MAIN_ACTIONS]


def test_every_main_button_maps_to_a_real_intent():
    for action, _ in copy.MAIN_ACTIONS:
        assert Intent(action) in Intent


def test_help_carries_the_not_a_trading_app_disclaimer():
    text = render_plain(copy.help_message())
    assert "not a trading or investment app" in text
    assert "stock" in text and "policy" in text


def test_the_bot_never_says_invalid_command():
    text = render_plain(copy.fallback()).lower()
    for forbidden in ("invalid", "error", "unrecognised", "unrecognized", "not understood"):
        assert forbidden not in text
    assert copy.fallback().keyboard is not None


def test_every_standing_reply_carries_its_disclaimer():
    """Standing constraint 6 of the build plan."""
    for message in (copy.welcome(), copy.help_message()):
        assert copy.DISCLAIMER in render_plain(message)


def test_every_english_string_has_a_hindi_one():
    """A half-translated bot is worse than an English one — it looks broken mid-sentence."""
    english = set(copy._STRINGS[Language.ENGLISH])
    hindi = set(copy._STRINGS[Language.HINDI])
    assert english - hindi == set(), f"untranslated: {sorted(english - hindi)}"


def test_hindi_is_devanagari_not_transliterated_hinglish():
    """Somebody who reads Hindi reads Devanagari. "Namaste ji" is not a translation."""
    devanagari = range(0x0900, 0x097F)
    for key in ("greeting", "trust_1", "do_scam", "home_title"):
        text = copy.t(key, Language.HINDI)
        assert any(ord(ch) in devanagari for ch in text), f"{key} is not in Devanagari: {text}"


def test_the_hindi_welcome_still_leads_with_the_three_promises():
    """§8's trust paragraph must survive translation, in the same order."""
    message = copy.welcome(Language.HINDI)
    rendered = render_plain(message)
    first, second, third = copy.trust_lines(Language.HINDI)
    assert rendered.index(first) < rendered.index(second) < rendered.index(third)
    # And it must still refuse to ask for a credential — the promise, in Hindi.
    assert "ओटीपी" in first and "पिन" in first


def test_a_missing_hindi_key_falls_back_to_english_rather_than_breaking():
    """The fallback stays load-bearing: a key added in English tomorrow must not crash."""
    assert copy.t("greeting", Language.HINDI) != copy.t("greeting", Language.ENGLISH)
    copy._STRINGS[Language.ENGLISH]["_probe_only"] = "English probe"
    try:
        assert copy.t("_probe_only", Language.HINDI) == "English probe"
    finally:
        del copy._STRINGS[Language.ENGLISH]["_probe_only"]


def test_a_feature_prompt_exists_for_every_main_button():
    for action, _ in copy.MAIN_ACTIONS:
        message = copy.prompt_for(action)
        assert message.blocks, f"no prompt copy for {action}"
        assert message != copy.fallback()


def test_keyboard_rows_flatten_in_reading_order():
    keyboard = Keyboard.of(
        [Button(label="a", action="1"), Button(label="b", action="2")],
        [Button(label="c", action="3")],
    )
    assert [b.label for b in keyboard.buttons] == ["a", "b", "c"]
