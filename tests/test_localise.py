"""Translating a finished analysis — and refusing to, when a number moved.

The engines write English. A Tamil speaker should still get the loan breakdown, so the
last step re-words it. The tests that matter here are the refusals: a translation that
changed ₹5,333, dropped a sentence, or added one of its own must be thrown away and
the English sent instead.
"""

from __future__ import annotations

from app.channels.base import Block, Button, Keyboard, OutboundImage, OutboundMessage
from app.llm.base import LLMUnavailable
from app.llm.localise import Translation, localise, needs_translation
from app.llm.mock_driver import MockProvider

ANALYSIS = OutboundMessage.of(
    Block.heading("What this loan really costs"),
    Block.key_values(
        (
            ("Monthly EMI", "₹5,333"),
            ("Total repayment", "₹1,27,992"),
            ("True annual rate", "24.92% reducing"),
        )
    ),
    Block.para("The advertised 14% flat rate is really 24.92% a year."),
    Block.disclaimer("Educational guidance, not financial or legal advice."),
    keyboard=Keyboard.of([Button(label="📄 Check another", action="loan_analysis")]),
)


def _texts(message: OutboundMessage) -> str:
    parts: list[str] = []
    for block in message.blocks:
        parts.append(block.text)
        parts.extend(block.items)
        for key, value in block.pairs:
            parts.extend((key, value))
    return " ".join(parts)


def test_english_is_never_sent_to_the_model():
    provider = MockProvider()
    assert localise(ANALYSIS, "en", provider) is ANALYSIS
    assert provider.call_count == 0


def test_no_provider_means_the_english_goes_out_unchanged():
    assert localise(ANALYSIS, "ta", None) is ANALYSIS


def test_needs_translation_only_for_a_real_other_language():
    assert needs_translation("ta") is True
    assert needs_translation("hi") is True
    assert needs_translation("en") is False
    assert needs_translation("") is False


def test_a_good_translation_replaces_every_string_in_place():
    originals = [
        "What this loan really costs",
        "",
        "Monthly EMI",
        "₹5,333",
        "Total repayment",
        "₹1,27,992",
        "True annual rate",
        "24.92% reducing",
        "The advertised 14% flat rate is really 24.92% a year.",
        "Educational guidance, not financial or legal advice.",
        "📄 Check another",
    ]
    # Same numbers, "translated" words — a faithful translation, in the shape we demand.
    translated = [text.replace("really", "asalii").replace("costs", "laagat") for text in originals]
    provider = MockProvider().queue(Translation(strings=translated))

    result = localise(ANALYSIS, "hi", provider)

    assert "asalii" in _texts(result)
    assert "₹5,333" in _texts(result)
    assert result.keyboard is not None
    # The structure is untouched: same block kinds, same pair count, same button action.
    assert [b.kind for b in result.blocks] == [b.kind for b in ANALYSIS.blocks]
    assert result.keyboard.buttons[0].action == "loan_analysis"


def test_a_translation_that_changes_a_rupee_figure_is_thrown_away():
    """The one failure that could mislead somebody about their own money."""
    bad = [
        "इस लोन की असली लागत",
        "",
        "मासिक ईएमआई",
        "₹5,533",  # ← was ₹5,333
        "कुल भुगतान",
        "₹1,27,992",
        "असली सालाना दर",
        "24.92% घटती",
        "विज्ञापित 14% फ्लैट दर असल में 24.92% सालाना है।",
        "शिक्षा के लिए मार्गदर्शन, वित्तीय या कानूनी सलाह नहीं।",
        "📄 दूसरा जाँचें",
    ]
    provider = MockProvider().queue(Translation(strings=bad))

    result = localise(ANALYSIS, "hi", provider)

    assert result is ANALYSIS, "a changed number must send the English instead"
    assert "₹5,333" in _texts(result)
    assert "₹5,533" not in _texts(result)


def test_sentence_ending_punctuation_is_not_mistaken_for_part_of_the_number():
    """Found against live Gemini: every correct Hindi translation was being refused.

    An earlier number pattern ate the punctuation after a number, so "₹1,00,000." at the
    end of an English sentence and "₹1,00,000।" in Hindi looked like two different
    amounts. The guardrail fired on every real loan analysis and the Hindi user got
    English — a validator strict enough to reject all correct answers is a broken one.
    """
    message = OutboundMessage.of(Block.para("You will receive ₹97,500, not ₹1,00,000."))
    provider = MockProvider().queue(
        Translation(strings=["आपको ₹97,500 मिलेंगे, ₹1,00,000 नहीं।"])
    )

    result = localise(message, "hi", provider)

    assert result is not message, "punctuation differences must not refuse a translation"
    assert "₹97,500" in _texts(result)


def test_word_order_may_change_because_that_is_what_translation_does():
    """"₹31,992 more over 2 years" legitimately reorders. Only the multiset must hold."""
    message = OutboundMessage.of(Block.para("You repay ₹31,992 more over 2 years."))
    provider = MockProvider().queue(
        Translation(strings=["2 साल में आप ₹31,992 ज़्यादा चुकाते हैं।"])
    )

    result = localise(message, "hi", provider)

    assert result is not message
    assert "₹31,992" in _texts(result)


def test_a_swapped_pair_of_amounts_is_still_refused():
    """Reordering a clause is fine; swapping which amount is which is not.

    The multiset check cannot see a swap of two numbers on its own, so this pins the
    behaviour that does catch it: the values themselves must still all be present, and
    a translation that invents one is refused.
    """
    message = OutboundMessage.of(Block.para("EMI ₹5,333 and total ₹1,27,992."))
    provider = MockProvider().queue(Translation(strings=["ईएमआई ₹5,333 और कुल ₹1,27,993।"]))
    assert localise(message, "hi", provider) is message


def test_a_translation_that_adds_its_own_sentence_is_refused():
    """An extra string means the model added advice of its own."""
    provider = MockProvider().queue(
        Translation(strings=["one", "two", "and here is my own tip"])
    )
    assert localise(ANALYSIS, "ta", provider) is ANALYSIS


def test_a_translation_that_drops_a_sentence_is_refused():
    originals_count = 11
    dropped = ["x"] * originals_count
    dropped[0] = "   "  # the heading came back blank
    # Keep the numbers identical so only the dropped string can fail it.
    dropped[3], dropped[5], dropped[7] = "₹5,333", "₹1,27,992", "24.92"
    dropped[8] = "14 24.92"
    provider = MockProvider().queue(Translation(strings=dropped))

    assert localise(ANALYSIS, "ta", provider) is ANALYSIS


def test_a_model_failure_sends_the_english_rather_than_nothing():
    class Broken(MockProvider):
        def analyse(self, prompt, context, schema):
            raise LLMUnavailable("down")

    assert localise(ANALYSIS, "ta", Broken()) is ANALYSIS


def test_the_strings_go_as_fenced_data_with_the_target_language():
    provider = MockProvider().queue(Translation(strings=[]))
    localise(ANALYSIS, "mr", provider)
    call = provider.calls_to("analyse")[0]
    assert call.context["target_language"] == "mr"
    assert "Monthly EMI" in call.context["strings"]


def test_a_chart_keeps_its_bytes_and_only_its_caption_is_translated():
    message = OutboundMessage.of(
        Block.para("Where your ₹25,000 goes"),
        images=(OutboundImage(png=b"\x89PNG-not-really", caption="Your month"),),
    )
    provider = MockProvider().queue(
        Translation(strings=["आपके ₹25,000 कहाँ जाते हैं", "आपका महीना"])
    )

    result = localise(message, "hi", provider)

    assert result.images[0].png == b"\x89PNG-not-really"
    assert result.images[0].caption == "आपका महीना"


def test_a_message_with_nothing_to_translate_is_not_sent_at_all():
    """A divider-only message would waste a call."""
    provider = MockProvider()
    message = OutboundMessage.of(Block.divider())
    assert localise(message, "ta", provider) is message
    assert provider.call_count == 0
