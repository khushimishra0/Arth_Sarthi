"""The blend, the bands, and the two guardrails that keep the number honest.

`final = 0.6 × rules + 0.4 × model`, from §4. The split is the whole argument:
rules alone miss a scam pattern nobody has written a regex for yet; a model alone
is unexplainable, drifts between versions, and cannot be shown to a user. Together
the score is defensible *and* adaptive, and 60% of it always traces to a named rule.

Two guardrails sit on top, and both exist to fail in the safe direction.

**Disagreement.** If the rules and the model are more than 40 points apart, one of
them is badly wrong and we do not know which. Asserting a confident average of two
irreconcilable answers is the worst thing available, so the response is downgraded
to "Caution — needs human review" and says so.

**The false-positive floor (§13).** Nothing goes above 50 on one rule alone. A
single 25-point rule plus an enthusiastic model would otherwise be enough to call a
real bank message a scam, and *that* error is the one that destroys the product:
a user who is told their genuine bank SMS is fraud learns to ignore us, and the
next warning is the one that mattered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings, get_settings
from app.features.scam.rules import FiredRule, rule_score

__all__ = ["Band", "ScamAssessment", "score"]

FALSE_POSITIVE_CEILING = 50


class Band(StrEnum):
    """§4's four bands. The lowest one never says "safe" — see `headline`."""

    LIKELY_SAFE = "likely_safe"
    BE_CAREFUL = "be_careful"
    HIGH_RISK = "high_risk"
    ALMOST_CERTAINLY_SCAM = "almost_certainly_scam"
    NEEDS_REVIEW = "needs_review"

    @property
    def headline(self) -> str:
        return {
            Band.LIKELY_SAFE: "🟢 NO MAJOR RED FLAGS FOUND",
            Band.BE_CAREFUL: "🟡 BE CAREFUL",
            Band.HIGH_RISK: "🟠 HIGH RISK",
            Band.ALMOST_CERTAINLY_SCAM: "🔴 ALMOST CERTAINLY A SCAM",
            Band.NEEDS_REVIEW: "⚪ CAUTION — NEEDS HUMAN REVIEW",
        }[self]

    @property
    def is_top(self) -> bool:
        """The band that shows 1930 and cybercrime.gov.in."""
        return self is Band.ALMOST_CERTAINLY_SCAM


def band_for(value: int) -> Band:
    if value <= 25:
        return Band.LIKELY_SAFE
    if value <= 50:
        return Band.BE_CAREFUL
    if value <= 75:
        return Band.HIGH_RISK
    return Band.ALMOST_CERTAINLY_SCAM


@dataclass(frozen=True, slots=True)
class ScamAssessment:
    """A score, its band, and every reason behind it. Nothing here is unexplained."""

    score: int
    band: Band
    fired: tuple[FiredRule, ...]
    rule_score: int
    llm_score: int | None
    llm_used: bool
    downgraded: bool = False
    downgrade_reason: str = ""
    capped: bool = False

    @property
    def reasons(self) -> tuple[str, ...]:
        """The user-facing sentences, ranked by weight."""
        return tuple(f.user_sentence for f in self.fired)


def score(
    fired: list[FiredRule],
    llm_score: int | None = None,
    settings: Settings | None = None,
) -> ScamAssessment:
    """Blend the rule score with the model's opinion and apply both guardrails.

    `llm_score=None` means the model was not consulted — it was skipped to save
    money, or it was unavailable. The rule score then stands alone, which is a
    working product rather than an outage.
    """
    settings = settings or get_settings()
    rules_total = rule_score(fired)

    if llm_score is None:
        return _finalise(
            raw=rules_total,
            fired=fired,
            rules_total=rules_total,
            llm_score=None,
            llm_used=False,
        )

    weight = settings.scam_rule_weight
    blended = round(weight * rules_total + (1 - weight) * llm_score)

    gap = abs(rules_total - llm_score)
    if gap > settings.scam_disagreement_downgrade:
        return ScamAssessment(
            score=blended,
            band=Band.NEEDS_REVIEW,
            fired=tuple(fired),
            rule_score=rules_total,
            llm_score=llm_score,
            llm_used=True,
            downgraded=True,
            downgrade_reason=(
                f"the checks and the AI disagree by {gap} points "
                f"(rules {rules_total}, AI {llm_score})"
            ),
        )

    return _finalise(
        raw=blended,
        fired=fired,
        rules_total=rules_total,
        llm_score=llm_score,
        llm_used=True,
    )


def _finalise(
    *,
    raw: int,
    fired: list[FiredRule],
    rules_total: int,
    llm_score: int | None,
    llm_used: bool,
    settings: Settings | None = None,
) -> ScamAssessment:
    """Apply the §13 floor, then band the result."""
    settings = settings or get_settings()

    capped = False
    final = raw
    if raw > FALSE_POSITIVE_CEILING and len(fired) < settings.scam_min_rules_above_50:
        # One rule is a suspicion, not a verdict. Hold it at the top of "be careful"
        # so the user is still warned — the reasons are shown either way.
        final = FALSE_POSITIVE_CEILING
        capped = True

    return ScamAssessment(
        score=final,
        band=band_for(final),
        fired=tuple(fired),
        rule_score=rules_total,
        llm_score=llm_score,
        llm_used=llm_used,
        capped=capped,
    )
