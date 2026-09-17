"""Stage 2 — ordering the survivors. §6's weights, unchanged.

    purpose match ×3 · benefit size relative to income ×2 · specificity ×2 · ease ×1

Everything reaching this module is already *eligible*; ranking only decides what to
show first, and §6 asks for the top 5. The weights encode a view worth stating: what
the user came for matters three times more than how easy the form is, because a
scholarship worth ₹40,000 is worth an afternoon of paperwork and a ₹500 subsidy is
not.

"Benefit relative to income" is the term that makes this useful at ₹12,000 a month.
₹6,000 a year from PM-KISAN is 4% of a ₹1.44 lakh income and would be a rounding
error at ₹15 lakh. Ranking by absolute rupees would bury it under a ₹7.5 lakh loan
the user may not want.

Every component is exposed on `Ranked` so the score can be explained rather than
asserted — the same discipline as the scam scorer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.schemes.schema import ANY_CATEGORY, Scheme, SchemeProfile
from app.models.enums import income_band_annual_range

__all__ = ["MAX_RESULTS", "Ranked", "rank_schemes"]

MAX_RESULTS = 5

WEIGHT_PURPOSE = 3.0
WEIGHT_BENEFIT = 2.0
WEIGHT_SPECIFICITY = 2.0
WEIGHT_EASE = 1.0

# A benefit worth this share of annual income or more scores full marks on size.
# 25% of a year's income is life-changing at this level; more is not more useful.
_BENEFIT_SATURATION = 0.25


@dataclass(frozen=True, slots=True)
class Ranked:
    """A scheme with its score and the four components behind it."""

    scheme: Scheme
    score: float
    purpose: float
    benefit: float
    specificity: float
    ease: float

    @property
    def is_best_match(self) -> bool:
        """Whether the purpose term fired — what earns the "Best match" label."""
        return self.purpose > 0


def _purpose_score(scheme: Scheme, profile: SchemeProfile) -> float:
    """1.0 when the scheme's category is what the user asked for, else 0.

    Deliberately binary. A partial-credit table mapping "housing" to "savings" would
    be a judgement call dressed as arithmetic, and the user told us what they wanted.
    """
    if not profile.needs:
        return 0.0
    wanted = {need.value for need in profile.needs}
    if scheme.category in wanted:
        return 1.0
    if any(purpose in wanted for purpose in scheme.eligibility.purpose):
        return 1.0
    return 0.0


def _benefit_score(scheme: Scheme, profile: SchemeProfile) -> float:
    """How large the benefit is *for this person*, 0–1.

    Without an income band there is nothing to compare against, so this returns a
    neutral 0.5 rather than 0 — an unanswered question must not push a scheme down
    the list.
    """
    if scheme.benefit_amount is None:
        return 0.4  # real but unquantified: a KCC limit, an insurance cover
    if profile.income_band is None:
        return 0.5

    low, high = income_band_annual_range(profile.income_band)
    annual_income = (low + high) / 2 if high is not None else low * 1.5
    if annual_income <= 0:
        return 0.5

    ratio = scheme.benefit_amount / annual_income
    return min(1.0, ratio / _BENEFIT_SATURATION)


def _specificity_score(scheme: Scheme, profile: SchemeProfile) -> float:
    """How closely this scheme is aimed at *this* person, 0–1.

    A women-only scheme shown to a woman is a better match than a scheme open to
    everyone. Each narrowing dimension the profile actually satisfies adds a point.
    """
    points = 0.0
    possible = 0.0
    rules = scheme.eligibility

    possible += 1
    if rules.gender != "any" and profile.gender is not None:
        points += 1

    possible += 1
    if rules.area != "both" and profile.area is not None:
        points += 1

    possible += 1
    if not scheme.covers_all_states and profile.state is not None:
        points += 1

    possible += 1
    if ANY_CATEGORY not in {c.upper() for c in rules.caste_category}:
        points += 1

    possible += 1
    if rules.income_max_annual is not None:
        points += 1

    return points / possible if possible else 0.0


def _ease_score(scheme: Scheme) -> float:
    """1.0 for a form and a helpline, 0.0 for a committee and a site visit."""
    return (5 - scheme.application_difficulty) / 4.0


def rank_schemes(
    schemes: tuple[Scheme, ...],
    profile: SchemeProfile,
    limit: int = MAX_RESULTS,
) -> tuple[Ranked, ...]:
    """Score and order. Ties break on scheme id so the order is stable across runs."""
    ranked: list[Ranked] = []

    for scheme in schemes:
        purpose = _purpose_score(scheme, profile)
        benefit = _benefit_score(scheme, profile)
        specificity = _specificity_score(scheme, profile)
        ease = _ease_score(scheme)

        score = (
            WEIGHT_PURPOSE * purpose
            + WEIGHT_BENEFIT * benefit
            + WEIGHT_SPECIFICITY * specificity
            + WEIGHT_EASE * ease
        )
        ranked.append(
            Ranked(
                scheme=scheme,
                score=round(score, 4),
                purpose=purpose,
                benefit=round(benefit, 4),
                specificity=round(specificity, 4),
                ease=round(ease, 4),
            )
        )

    ranked.sort(key=lambda r: (-r.score, r.scheme.id))
    return tuple(ranked[:limit])
