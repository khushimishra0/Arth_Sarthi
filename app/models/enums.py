"""Closed vocabularies shared by the database, the channels and the features.

A free-text column where a fixed set exists is how one profile ends up holding
"Bihar", "bihar" and "BIHAR" as three different states, and how the Phase 6
eligibility engine silently stops matching. Everything with a finite answer set
is an enum, defined once, here.

Income is the one that matters for privacy. §10 of the plan is explicit: a profile
stores an income *band*, never an exact figure. The band still has to be usable —
scheme eligibility is written against annual ceilings like "family income under
₹8 lakh" — so each band carries its monthly bounds and the Phase 6 comparison is
made against those bounds rather than against a number we refused to collect.

(The `budgets` table does store exact rupee figures. That is not a contradiction:
those are numbers the user typed in order to be given a chart of them. A profile
is data we keep about a person; a budget is a calculation they asked for.)
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "INCOME_BAND_MONTHLY_RANGE",
    "AgeBand",
    "Area",
    "CasteCategory",
    "Channel",
    "Feature",
    "Gender",
    "IncomeBand",
    "Language",
    "NeedCategory",
    "income_band_annual_range",
]


class Channel(StrEnum):
    """Where a user reached us. One user record per (channel, channel_user_id)."""

    TELEGRAM = "telegram"
    WEB = "web"
    WHATSAPP = "whatsapp"  # not built; the adapter exists so this stays a one-file port


class Language(StrEnum):
    ENGLISH = "en"
    HINDI = "hi"


class Gender(StrEnum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class AgeBand(StrEnum):
    UNDER_18 = "under_18"
    AGE_18_25 = "18_25"
    AGE_26_35 = "26_35"
    AGE_36_45 = "36_45"
    AGE_46_60 = "46_60"
    OVER_60 = "over_60"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class Area(StrEnum):
    """Rural/urban decides eligibility for a large share of central schemes."""

    RURAL = "rural"
    URBAN = "urban"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class IncomeBand(StrEnum):
    """Monthly household income. Bounds live in INCOME_BAND_MONTHLY_RANGE."""

    UNDER_10K = "under_10k"
    FROM_10K_TO_25K = "10k_25k"
    FROM_25K_TO_50K = "25k_50k"
    FROM_50K_TO_1L = "50k_1l"
    OVER_1L = "over_1l"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class CasteCategory(StrEnum):
    GENERAL = "general"
    OBC = "obc"
    SC = "sc"
    ST = "st"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class NeedCategory(StrEnum):
    """Question 5 of the scheme profile — what the user came for."""

    EDUCATION = "education"
    BUSINESS = "business"
    HOUSING = "housing"
    HEALTH = "health"
    PENSION = "pension"
    FARMING = "farming"
    SAVINGS = "savings"


class Feature(StrEnum):
    """Which engine produced a stored analysis."""

    SCAM = "scam"
    LOAN = "loan"
    SCHEMES = "schemes"
    BUDGET = "budget"
    CONCIERGE = "concierge"  # the LLM that reads a free-text message and routes it
    TRANSLATION = "translation"  # re-wording a finished answer into the user's language


# Monthly household income, in rupees: (inclusive low, exclusive high).
# `None` as the high bound means unbounded above.
# PREFER_NOT_TO_SAY spans everything on purpose — declining to answer must widen
# the result set, never empty it.
INCOME_BAND_MONTHLY_RANGE: dict[IncomeBand, tuple[int, int | None]] = {
    IncomeBand.UNDER_10K: (0, 10_000),
    IncomeBand.FROM_10K_TO_25K: (10_000, 25_000),
    IncomeBand.FROM_25K_TO_50K: (25_000, 50_000),
    IncomeBand.FROM_50K_TO_1L: (50_000, 100_000),
    IncomeBand.OVER_1L: (100_000, None),
    IncomeBand.PREFER_NOT_TO_SAY: (0, None),
}


def income_band_annual_range(band: IncomeBand) -> tuple[int, int | None]:
    """Annualised bounds, for comparison against scheme income ceilings.

    >>> income_band_annual_range(IncomeBand.FROM_10K_TO_25K)
    (120000, 300000)
    """
    low, high = INCOME_BAND_MONTHLY_RANGE[band]
    return low * 12, None if high is None else high * 12
