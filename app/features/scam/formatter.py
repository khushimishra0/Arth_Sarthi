"""The §4 response, as channel-neutral blocks.

Score, band, ranked reasons, what to do now, helpline on the top band, disclaimer.
No Telegram markup — Telegram, the Phase 7 dashboard and any future channel render
the same object.

The ordering is the product. §4: *"a user who is told 91% learns nothing and must
come back next time. A user who is told guaranteed returns are always a lie has been
taught a rule they can apply themselves forever."* So the number is one line and the
reasons get the space. Each response is written to leave the user slightly harder to
defraud than it found them.

Two words that never appear in the lowest band: "safe" and "genuine". We did not
find red flags — that is not the same as having verified anything, and a product
that says "safe" owns whatever happens next.
"""

from __future__ import annotations

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.features.scam.scorer import Band, ScamAssessment

__all__ = ["CYBERCRIME_PORTAL", "HELPLINE", "format_assessment", "format_unreadable"]

HELPLINE = "1930"
CYBERCRIME_PORTAL = "cybercrime.gov.in"

DISCLAIMER = "ArthaSathi gives educational guidance, not legal advice."

_NEXT_STEPS: dict[Band, tuple[str, ...]] = {
    Band.ALMOST_CERTAINLY_SCAM: (
        "Do not pay anything, however small the amount sounds.",
        "Do not share your OTP, PIN, CVV or card number with anyone.",
        "Block the sender and report them inside WhatsApp.",
        f"If you have already paid — call {HELPLINE} now and file at {CYBERCRIME_PORTAL}. "
        "Money reported in the first few hours is sometimes recovered.",
    ),
    Band.HIGH_RISK: (
        "Do not pay and do not share any personal details yet.",
        "Check the company yourself: search its name plus the word “fraud”.",
        "If it claims SEBI registration, verify the number free at sebi.gov.in.",
        "Show this to one person you trust before you act on it.",
    ),
    Band.BE_CAREFUL: (
        "Slow down. Nothing genuine gets worse for waiting a day.",
        "Verify independently — call the company on a number you found yourself, "
        "never the one in the message.",
        "Never share an OTP, PIN or password to “confirm” anything.",
    ),
    Band.LIKELY_SAFE: (
        "I found no major red flags — but I have not verified who sent this.",
        "Before you pay anyone, check the account belongs to who you think it does.",
        "If money or an OTP gets involved later, send it to me again.",
    ),
    Band.NEEDS_REVIEW: (
        "Treat this as suspicious until someone you trust has looked at it.",
        "Do not pay and do not share an OTP while you are unsure.",
        "Show it to a family member, or call the company on a number you looked up "
        "yourself.",
    ),
}


def format_assessment(assessment: ScamAssessment, quoted_text: str = "") -> OutboundMessage:
    """Render a completed assessment for any channel."""
    blocks: list[Block] = []

    if quoted_text:
        blocks.append(Block.para(f"📷 “{_shorten(quoted_text)}”"))

    if assessment.band is Band.NEEDS_REVIEW:
        blocks.append(Block.heading(assessment.band.headline))
        blocks.append(
            Block.para(
                f"I cannot give you a confident number here — {assessment.downgrade_reason}. "
                "When my checks and my AI disagree this much, telling you a tidy "
                "percentage would be pretending. Here is what I did find:"
            )
        )
    else:
        blocks.append(Block.heading(f"{assessment.score}%  ·  {_headline(assessment)}"))

    if assessment.fired:
        blocks.append(Block.para("Why I think so:"))
        blocks.append(Block.bullets(tuple(f"🚩 {reason}" for reason in assessment.reasons)))
    else:
        blocks.append(
            Block.para(
                "I did not find any of the 14 warning signs I check for. That does not "
                "make it genuine — it means nothing obvious is wrong."
            )
        )

    if assessment.capped:
        blocks.append(
            Block.para(
                "Only one warning sign fired, so I am not calling this a scam outright — "
                "but read that reason carefully before you act."
            )
        )

    blocks.append(Block.para("What to do now:"))
    blocks.append(Block.steps(_NEXT_STEPS[assessment.band]))

    if assessment.band.is_top:
        blocks.append(Block.divider())
        blocks.append(
            Block.key_values(
                (
                    ("🆘 Cyber fraud helpline", HELPLINE),
                    ("🌐 File a complaint", CYBERCRIME_PORTAL),
                )
            )
        )

    blocks.append(Block.disclaimer(DISCLAIMER))

    keyboard = Keyboard.of(
        [Button(label="📄 This is a loan document", action="loan_analysis")],
        [Button(label="🚩 Check another message", action="scam_check")],
    )
    if assessment.band.is_top:
        keyboard = Keyboard.of(
            [Button(label=f"🌐 Report at {CYBERCRIME_PORTAL}", url=f"https://{CYBERCRIME_PORTAL}")],
            [Button(label="🚩 Check another message", action="scam_check")],
        )

    return OutboundMessage.of(*blocks, keyboard=keyboard)


def format_unreadable(reason: str = "") -> OutboundMessage:
    """When the image could not be read at all. Never a naked error — §8."""
    return OutboundMessage.of(
        Block.para(
            "I could not read that image clearly enough to check it. That is my "
            "problem, not yours."
        ),
        Block.para("What usually fixes it:"),
        Block.bullets(
            (
                "Take the screenshot again with the whole message visible.",
                "Make sure the text is in focus and not cut off at the edges.",
                "Or just type out what the message said — I can check that too.",
            )
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=Keyboard.of([Button(label="🚩 Try again", action="scam_check")]),
    )


def _headline(assessment: ScamAssessment) -> str:
    """The band's headline, corrected for the one case where it would contradict itself.

    "NO MAJOR RED FLAGS FOUND" printed directly above a 🚩 is the kind of detail
    that makes a user stop believing the rest of the message. A low score with
    something showing is "nothing serious, but look at this" — which is what the
    number actually means.
    """
    if assessment.band is Band.LIKELY_SAFE and assessment.fired:
        return "🟢 NOTHING SERIOUS — BUT ONE THING TO CHECK"
    return assessment.band.headline


def _shorten(text: str, limit: int = 220) -> str:
    """Quote enough that the user recognises their own message, no more."""
    flattened = " ".join(text.split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: limit - 1].rstrip() + "…"
