"""Orchestration: screenshot in, assessment out, as cheaply as honesty allows.

The order of operations is the cost control.

1. **Cache.** Content hash first. The screenshot circulating in a WhatsApp group is
   analysed once for everyone who forwards it.
2. **Downscale + EXIF strip.** Before anything is sent anywhere.
3. **Caps.** Checked before the call, so the cap is a cap and not a receipt.
4. **Extract.** One vision call, transcription only.
5. **Rules.** Free, deterministic, and they run on every message.
6. **Judge — conditionally.** Skipped when the rules already leave no doubt. An
   overwhelming rule score cannot be moved out of its band by a 40% opinion, so
   buying that opinion changes the answer by nothing and costs money every time.
7. **Blend, band, format.**

Everything below the feature layer can fail: the network, the key, the cap, the
model's grip on a schema. Each failure has a user-facing sentence and a next action
— never a stack trace, never a naked error.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.config import Settings, get_settings
from app.features.scam import prompts
from app.features.scam.formatter import DISCLAIMER, format_assessment, format_unreadable
from app.features.scam.rules import evaluate, rule_score
from app.features.scam.schema import ExtractedMessage, ScamJudgement
from app.features.scam.scorer import ScamAssessment, score
from app.llm import spend
from app.llm.base import (
    CapabilityError,
    LLMProvider,
    LLMUnavailable,
    LLMValidationError,
    SpendCapReached,
)
from app.llm.preprocess import ImagePrepError, prepare_image
from app.models import repo
from app.models.db import User
from app.models.enums import Feature
from app.utils.hashing import content_hash

__all__ = ["analyse_image", "analyse_text", "check_screenshot"]

log = logging.getLogger(__name__)

# Above this the rules alone are decisive: with the model's 40% pulling as hard as
# it can towards zero, the result still lands in the top band. Buying that opinion
# would change nothing, so it is not bought. (0.6 × 85 = 51, still "high risk".)
RULES_DECISIVE_THRESHOLD = 85


def _judge(provider: LLMProvider, message: ExtractedMessage) -> int | None:
    """Ask the model for its independent opinion. `None` if it could not be had."""
    try:
        judgement = provider.analyse(
            prompts.JUDGEMENT_PROMPT,
            {
                "message_text": message.message_text,
                "sender_name": message.sender_name,
                "links": message.links,
                "claimed_entity": message.claimed_entity,
                "payment_handles": message.payment_handles,
                "registration_number": message.registration_number,
            },
            ScamJudgement,
        )
    except (LLMUnavailable, LLMValidationError, CapabilityError) as exc:
        # A missing opinion is survivable — the rules still ran, and their score
        # stands alone. Degrading is better than failing.
        log.warning("scam judgement unavailable, falling back to rules only: %s", exc)
        return None
    return judgement.score


def assess(
    message: ExtractedMessage,
    provider: LLMProvider | None = None,
    session: Session | None = None,
    settings: Settings | None = None,
) -> ScamAssessment:
    """Rules, then the model if it would change anything, then the blend.

    Pure enough to test directly: hand it an `ExtractedMessage` and it never touches
    a network unless you gave it a provider that does.
    """
    settings = settings or get_settings()
    fired = evaluate(message)

    if provider is None or rule_score(fired) >= RULES_DECISIVE_THRESHOLD:
        return score(fired, llm_score=None, settings=settings)

    llm_score = _judge(provider, message)
    if llm_score is not None and session is not None:
        spend.record_call(session, provider, method="analyse", feature=Feature.SCAM)
    return score(fired, llm_score=llm_score, settings=settings)


def analyse_text(
    text: str,
    provider: LLMProvider | None = None,
    session: Session | None = None,
    settings: Settings | None = None,
) -> ScamAssessment:
    """Check a message the user typed or pasted. No vision call, so no image cost."""
    return assess(
        ExtractedMessage(message_text=text), provider=provider, session=session, settings=settings
    )


def analyse_image(
    image: bytes,
    provider: LLMProvider,
    session: Session | None = None,
    settings: Settings | None = None,
) -> tuple[ScamAssessment, ExtractedMessage]:
    """Full path: prepare, transcribe, assess. Raises `ImagePrepError` on a bad image."""
    settings = settings or get_settings()
    prepared = prepare_image(image, settings.image_max_edge_px)

    provider.require_vision()
    extracted = provider.read_image(prepared, prompts.EXTRACTION_PROMPT, ExtractedMessage)
    if session is not None:
        spend.record_call(session, provider, method="read_image", feature=Feature.SCAM)

    return (
        assess(extracted, provider=provider, session=session, settings=settings),
        extracted,
    )


async def check_screenshot(
    image: bytes,
    *,
    session: Session,
    user: User,
    provider: LLMProvider,
    settings: Settings | None = None,
) -> OutboundMessage:
    """The whole feature, as a channel would call it. Never raises.

    Runs the blocking provider call with `asyncio.to_thread` so one user's analysis
    does not freeze the bot for everyone else — the one place the sync/async
    boundary is crossed, as documented on `LLMProvider`.
    """
    settings = settings or get_settings()
    digest = content_hash(image)

    cached = repo.cached_analysis(session, feature=Feature.SCAM, input_hash=digest)
    if cached is not None:
        log.info("scam cache hit for %s", digest[:12])
        return _from_stored(cached.result_json)

    try:
        spend.check_caps(session, user, settings)
    except SpendCapReached as exc:
        return _cap_reached(exc)

    try:
        assessment, extracted = await asyncio.to_thread(
            analyse_image, image, provider, session, settings
        )
    except ImagePrepError:
        return format_unreadable()
    except CapabilityError as exc:
        log.error("scam check misconfigured: %s", exc)
        return _misconfigured()
    except (LLMUnavailable, LLMValidationError) as exc:
        log.warning("scam check failed: %s", exc)
        return _temporarily_unavailable()

    repo.record_analysis(
        session,
        user,
        feature=Feature.SCAM,
        input_hash=digest,
        result=_to_stored(assessment, extracted),
    )
    return format_assessment(assessment, extracted.message_text)


# ---------------------------------------------------------------------------
# Cache serialisation
#
# The stored result holds the transcribed message text, because re-rendering it is
# the whole point of the cache. It does not hold the image, and the row it lives in
# is deleted after 90 days.
# ---------------------------------------------------------------------------


def _to_stored(assessment: ScamAssessment, extracted: ExtractedMessage) -> dict:
    return {
        "score": assessment.score,
        "band": assessment.band.value,
        "rule_score": assessment.rule_score,
        "llm_score": assessment.llm_score,
        "llm_used": assessment.llm_used,
        "downgraded": assessment.downgraded,
        "downgrade_reason": assessment.downgrade_reason,
        "capped": assessment.capped,
        "message_text": extracted.message_text,
        "fired": [
            {
                "id": f.id,
                "title": f.title,
                "weight": f.weight,
                "evidence": f.evidence,
                "sentence": f.sentence,
            }
            for f in assessment.fired
        ],
    }


def _from_stored(stored: dict) -> OutboundMessage:
    from app.features.scam.rules import FiredRule
    from app.features.scam.scorer import Band

    assessment = ScamAssessment(
        score=stored["score"],
        band=Band(stored["band"]),
        fired=tuple(
            FiredRule(
                id=f["id"],
                title=f["title"],
                weight=f["weight"],
                evidence=f["evidence"],
                sentence=f["sentence"],
            )
            for f in stored.get("fired", [])
        ),
        rule_score=stored.get("rule_score", 0),
        llm_score=stored.get("llm_score"),
        llm_used=stored.get("llm_used", False),
        downgraded=stored.get("downgraded", False),
        downgrade_reason=stored.get("downgrade_reason", ""),
        capped=stored.get("capped", False),
    )
    return format_assessment(assessment, stored.get("message_text", ""))


# ---------------------------------------------------------------------------
# Failure paths — every one of them ends in something the user can do next
# ---------------------------------------------------------------------------


def _retry_keyboard() -> Keyboard:
    return Keyboard.of([Button(label="🚩 Try again", action="scam_check")])


def _cap_reached(exc: SpendCapReached) -> OutboundMessage:
    log.warning("scam check refused: %s", exc)
    return OutboundMessage.of(
        Block.para(
            "I have hit my checking limit for now, so I cannot read images at the "
            "moment. This is my limit, not anything about your message."
        ),
        Block.para(
            "You can still type or paste the message text and I will run my 14 "
            "fraud checks on it — those are free and catch most of what arrives."
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )


def _temporarily_unavailable() -> OutboundMessage:
    return OutboundMessage.of(
        Block.para(
            "I could not reach my reading service just now. Nothing you sent was "
            "saved, and this will usually work if you try again in a minute."
        ),
        Block.para("If it is urgent: type out what the message said and I will check the text."),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )


def _misconfigured() -> OutboundMessage:
    """A configuration error is ours. The user still gets a way forward."""
    return OutboundMessage.of(
        Block.para(
            "I am not set up to read images right now — that is a problem on my "
            "side and someone has been told."
        ),
        Block.para("Type out what the message said and I will still check it for you."),
        Block.disclaimer(DISCLAIMER),
        keyboard=_retry_keyboard(),
    )
