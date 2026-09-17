"""Stage 1 — the hard filters. §6: *"This is code, not judgement."*

A scheme is dropped if gender, age, state, rural/urban, income ceiling or caste
category does not match. No scoring, no "probably", no model. A user is never shown
something they cannot get, because being sent to a bank counter and turned away is
worse than not being told about the scheme at all.

Two rules govern every check below:

**Unknown widens.** An unanswered question cannot disqualify anything. Every
question offers "prefer not to say", and §6 says that choice must widen results — so
`None` on either side of a comparison means "keep it".

**Income is a band, not a figure.** §10 stores income as a band on purpose. A band
overlapping a scheme's ceiling is kept, not dropped: someone in the ₹10k–25k band
might be under an ₹8 lakh/year ceiling, and rejecting them because the *top* of
their band exceeds it would hide a scheme they qualify for.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.schemes.schema import ANY_CATEGORY, Scheme, SchemeProfile
from app.models.enums import AgeBand, Area, CasteCategory, Gender, income_band_annual_range

__all__ = ["Disqualification", "eligible_schemes", "explain_rejection", "is_eligible"]

# Age bands to the (min, max) years they span. `None` above OVER_60 means no ceiling.
_AGE_SPAN: dict[AgeBand, tuple[int, int | None]] = {
    AgeBand.UNDER_18: (0, 17),
    AgeBand.AGE_18_25: (18, 25),
    AgeBand.AGE_26_35: (26, 35),
    AgeBand.AGE_36_45: (36, 45),
    AgeBand.AGE_46_60: (46, 60),
    AgeBand.OVER_60: (61, None),
}

_CASTE_CODE: dict[CasteCategory, str] = {
    CasteCategory.GENERAL: "GENERAL",
    CasteCategory.OBC: "OBC",
    CasteCategory.SC: "SC",
    CasteCategory.ST: "ST",
}


@dataclass(frozen=True, slots=True)
class Disqualification:
    """Why one scheme was dropped. Not shown to the user — used for the Gate 6 report
    and for debugging a filter that is too aggressive."""

    scheme_id: str
    reason: str


def _gender_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    required = scheme.eligibility.gender
    if required == "any" or profile.gender is None:
        return None
    if profile.gender is Gender.OTHER:
        # A women-only scheme is a legal category, and we cannot decide on someone's
        # behalf whether they fall inside it. Keep it and let them read the criteria.
        return None
    if profile.gender.value != required:
        return f"{scheme.name} is for {required} applicants only"
    return None


def _age_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    rules = scheme.eligibility
    if profile.age_band is None or (rules.age_min is None and rules.age_max is None):
        return None

    low, high = _AGE_SPAN[profile.age_band]

    # Overlap, not containment: a 26-35 applicant against a scheme for 18-30 still
    # has eligible years inside their band.
    if rules.age_max is not None and low > rules.age_max:
        return f"{scheme.name} is for applicants up to {rules.age_max}"
    if rules.age_min is not None and high is not None and high < rules.age_min:
        return f"{scheme.name} is for applicants {rules.age_min} and older"
    return None


def _state_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    if scheme.covers_all_states or profile.state is None:
        return None
    if profile.state.lower() not in {s.lower() for s in scheme.states}:
        return f"{scheme.name} runs only in {', '.join(scheme.states)}"
    return None


def _area_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    required = scheme.eligibility.area
    if required == "both" or profile.area is None:
        return None
    if profile.area is Area.PREFER_NOT_TO_SAY:
        return None
    if profile.area.value != required:
        return f"{scheme.name} is for {required} applicants"
    return None


def _income_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    ceiling = scheme.eligibility.income_max_annual
    if ceiling is None or profile.income_band is None:
        return None

    low, _high = income_band_annual_range(profile.income_band)
    # Compare the *floor* of the band. If even the lowest income in the band is above
    # the ceiling, nobody in it qualifies. Otherwise some of them do, and hiding the
    # scheme would cost those users a benefit they are entitled to.
    if low > ceiling:
        return f"{scheme.name} has an income ceiling of ₹{ceiling:,}/year"
    return None


def _caste_matches(scheme: Scheme, profile: SchemeProfile) -> str | None:
    if scheme.open_to_any_caste or profile.caste_category is None:
        return None
    code = _CASTE_CODE.get(profile.caste_category)
    if code is None:
        return None
    allowed = {c.upper() for c in scheme.eligibility.caste_category}
    if ANY_CATEGORY in allowed or code in allowed:
        return None
    return f"{scheme.name} is reserved for {', '.join(sorted(allowed))} applicants"


def _alternatives_match(scheme: Scheme, profile: SchemeProfile) -> str | None:
    """`any_of`: the applicant must satisfy at least one qualifying route.

    Unknown still widens. An alternative the profile cannot be judged against — the
    question was skipped — counts as satisfied, so declining to state a caste never
    hides a scheme.
    """
    alternatives = scheme.eligibility.any_of
    if not alternatives:
        return None

    for alternative in alternatives:
        if alternative.gender is not None:
            if profile.gender is None or profile.gender is Gender.OTHER:
                return None
            if profile.gender.value == alternative.gender:
                return None

        if alternative.caste_category is not None:
            if profile.caste_category is None:
                return None
            code = _CASTE_CODE.get(profile.caste_category)
            allowed = {c.upper() for c in alternative.caste_category}
            if code is None or ANY_CATEGORY in allowed or code in allowed:
                return None

        if alternative.area is not None:
            if profile.area is None:
                return None
            if profile.area.value == alternative.area:
                return None

    return f"{scheme.name} is limited to particular groups — see its conditions"


_CHECKS = (
    _gender_matches,
    _age_matches,
    _state_matches,
    _area_matches,
    _income_matches,
    _caste_matches,
    _alternatives_match,
)


def explain_rejection(scheme: Scheme, profile: SchemeProfile) -> str | None:
    """The first reason this scheme does not apply, or `None` if it does."""
    for check in _CHECKS:
        reason = check(scheme, profile)
        if reason is not None:
            return reason
    return None


def is_eligible(scheme: Scheme, profile: SchemeProfile) -> bool:
    return explain_rejection(scheme, profile) is None


def eligible_schemes(
    schemes: tuple[Scheme, ...], profile: SchemeProfile
) -> tuple[tuple[Scheme, ...], tuple[Disqualification, ...]]:
    """Split the dataset into what this person can get and what they cannot."""
    kept: list[Scheme] = []
    dropped: list[Disqualification] = []

    for scheme in schemes:
        reason = explain_rejection(scheme, profile)
        if reason is None:
            kept.append(scheme)
        else:
            dropped.append(Disqualification(scheme_id=scheme.id, reason=reason))

    return tuple(kept), tuple(dropped)
