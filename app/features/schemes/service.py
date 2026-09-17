"""Orchestration: filter → rank → explain → format.

The order is the guarantee. Eligibility runs before ranking so nothing ineligible can
be ranked into the top five; ranking runs before explanation so the model only ever
sees five rows it is allowed to talk about. The model is the last step and the least
trusted one — by the time it is called, the answer is already correct and it is only
making it readable.

Costs one cheap text call per run, and nothing at all when there is no key: the
explainer falls back to each record's own `benefit_text`.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.channels.base import OutboundMessage
from app.features.schemes.eligibility import Disqualification, eligible_schemes
from app.features.schemes.explainer import explain
from app.features.schemes.formatter import format_matches, format_no_matches, format_unavailable
from app.features.schemes.ranker import MAX_RESULTS, Ranked, rank_schemes
from app.features.schemes.schema import Scheme, SchemeProfile, load_schemes
from app.llm import spend
from app.llm.base import CapabilityError, LLMProvider, SpendCapReached
from app.models import repo
from app.models.db import User
from app.models.enums import Feature
from app.utils.hashing import content_hash

__all__ = ["find_schemes", "match_profile", "profile_summary"]

log = logging.getLogger(__name__)


def match_profile(
    profile: SchemeProfile,
    schemes: tuple[Scheme, ...] | None = None,
    limit: int = MAX_RESULTS,
) -> tuple[tuple[Ranked, ...], tuple[Disqualification, ...]]:
    """Filter then rank. Pure — no I/O, no model, no database."""
    dataset = schemes if schemes is not None else load_schemes()
    kept, dropped = eligible_schemes(dataset, profile)
    return rank_schemes(kept, profile, limit), dropped


def profile_summary(profile: SchemeProfile) -> dict:
    """What the model is told about the person. No name, no phone, no exact income."""
    return {
        "gender": profile.gender.value if profile.gender else "not stated",
        "age_band": profile.age_band.value if profile.age_band else "not stated",
        "state": profile.state or "not stated",
        "area": profile.area.value if profile.area else "not stated",
        "income_band": profile.income_band.value if profile.income_band else "not stated",
        "caste_category": profile.caste_category.value if profile.caste_category else "not stated",
        "needs": [need.value for need in profile.needs],
    }


async def find_schemes(
    profile: SchemeProfile,
    *,
    session: Session,
    user: User,
    provider: LLMProvider | None = None,
) -> OutboundMessage:
    """The whole feature, as a channel calls it. Never raises."""
    dataset = load_schemes()
    if not dataset:
        log.error("scheme dataset is empty or missing — refusing to guess")
        return format_unavailable()

    ranked, dropped = match_profile(profile, dataset)
    log.info(
        "schemes: %d of %d eligible, showing %d",
        len(dataset) - len(dropped),
        len(dataset),
        len(ranked),
    )

    if not ranked:
        return format_no_matches(profile)

    # The explanation is a nicety, so it is the first thing dropped when the budget
    # is gone. The cards are already correct without it.
    active = provider
    if active is not None:
        try:
            spend.check_caps(session, user)
        except SpendCapReached:
            log.info("scheme explanation skipped: spend cap reached")
            active = None

    explanations = await asyncio.to_thread(
        explain, ranked, profile_summary(profile), active
    )
    if active is not None:
        try:
            spend.record_call(session, active, method="analyse", feature=Feature.SCHEMES)
        except CapabilityError:  # pragma: no cover - defensive
            pass

    # Stored so `/schemes` a second time is instant and so the demo can show what was
    # matched. Only ids and the profile shape — no personal detail beyond the bands
    # already in `profiles`.
    repo.record_analysis(
        session,
        user,
        feature=Feature.SCHEMES,
        input_hash=content_hash(str(sorted(profile_summary(profile).items()))),
        result={
            "matched": [item.scheme.id for item in ranked],
            "scores": {item.scheme.id: item.score for item in ranked},
            "dataset_size": len(dataset),
        },
    )

    return format_matches(ranked, profile, explanations)
