"""The scheme record — §6, verbatim — and the profile it is matched against.

The whole feature rests on one decision from §6: *"asking an LLM 'which schemes suit
this person' produces confident, plausible, and sometimes entirely fictional answers
— wrong income ceilings, discontinued schemes, invented names. A rural woman denied
at a bank counter because ArthaSathi sent her after a scheme that does not exist is a
worse outcome than never having asked."*

So every scheme is a row in `data/schemes.json`, loaded and validated here. The model
never supplies a scheme; it is only allowed to explain rows it was handed. That is
why this file is strict about `source_url` and `last_verified` — a row nobody can
check is a row nobody should act on, and the verification date is shown to the user.

`ANY` / `ALL` / `both` are the widening sentinels. They matter because the profile
questions all offer "prefer not to say", and an unanswered question must widen the
result set rather than empty it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AgeBand, Area, CasteCategory, Gender, IncomeBand, NeedCategory

__all__ = [
    "SCHEMES_PATH",
    "Eligibility",
    "Scheme",
    "SchemeLevel",
    "SchemeProfile",
    "BenefitType",
    "load_schemes",
]

SCHEMES_PATH = Path(__file__).resolve().parents[3] / "data" / "schemes.json"

# The sentinels that mean "this field does not narrow anything".
ANY_STATE = "ALL"
ANY_CATEGORY = "ANY"


class SchemeLevel(StrEnum):
    CENTRAL = "central"
    STATE = "state"


class BenefitType(StrEnum):
    LOAN = "loan"
    SUBSIDY = "subsidy"
    GRANT = "grant"
    SCHOLARSHIP = "scholarship"
    INSURANCE = "insurance"
    PENSION = "pension"
    SAVINGS = "savings"


class Alternative(BaseModel):
    """One way of qualifying, when a scheme offers several.

    Stand-Up India is the case that forced this: it is for SC or ST applicants **or**
    for women of any category. Expressed as flat fields, `caste_category: [SC, ST]`
    wrongly excludes a General-category woman, and `[ANY]` wrongly includes a
    General-category man. Both are failures — one hides an entitlement, the other
    sends someone to a bank counter to be turned away.
    """

    gender: str | None = None
    caste_category: list[str] | None = None
    area: str | None = None


class Eligibility(BaseModel):
    """The hard filters. Every field is a *widening* default: absent means no limit."""

    gender: str = "any"
    age_min: int | None = None
    age_max: int | None = None
    income_max_annual: int | None = None
    area: str = "both"
    caste_category: list[str] = Field(default_factory=lambda: [ANY_CATEGORY])
    purpose: list[str] = Field(default_factory=list)
    # Qualifying by *any one* of these, on top of the base criteria above. Empty
    # means the base criteria are the whole test.
    any_of: list[Alternative] = Field(default_factory=list)
    # Free text the engine cannot check — shown to the user so *they* can.
    conditions: list[str] = Field(default_factory=list)

    @field_validator("gender")
    @classmethod
    def _known_gender(cls, value: str) -> str:
        allowed = {"any", "female", "male"}
        if value not in allowed:
            raise ValueError(f"gender must be one of {sorted(allowed)}, got {value!r}")
        return value

    @field_validator("area")
    @classmethod
    def _known_area(cls, value: str) -> str:
        allowed = {"both", "rural", "urban"}
        if value not in allowed:
            raise ValueError(f"area must be one of {sorted(allowed)}, got {value!r}")
        return value


class Scheme(BaseModel):
    """One verified row. Nothing here is generated; all of it is transcribed."""

    id: str
    name: str
    name_hi: str = ""
    ministry: str = ""
    level: SchemeLevel
    states: list[str] = Field(default_factory=lambda: [ANY_STATE])
    category: str
    benefit_type: BenefitType
    benefit_text: str
    # Rupee value of the benefit where it has one, for the ranker's "benefit size
    # relative to income" term. `None` for schemes whose value is not a lump sum.
    benefit_amount: int | None = None
    eligibility: Eligibility = Field(default_factory=Eligibility)
    documents_required: list[str] = Field(default_factory=list)
    how_to_apply: str = ""
    application_url: str = ""
    helpline: str = ""
    source_url: str
    last_verified: date
    # 1 = a form and a helpline; 5 = a committee and a site visit. Feeds `ease`.
    application_difficulty: int = Field(default=3, ge=1, le=5)

    @field_validator("source_url")
    @classmethod
    def _must_be_checkable(cls, value: str) -> str:
        """A row nobody can check is a row nobody should act on."""
        if not value.startswith("https://"):
            raise ValueError(f"source_url must be an https URL, got {value!r}")
        return value

    @property
    def covers_all_states(self) -> bool:
        return ANY_STATE in self.states

    @property
    def open_to_any_caste(self) -> bool:
        return ANY_CATEGORY in self.eligibility.caste_category


@dataclass(frozen=True, slots=True)
class SchemeProfile:
    """The six answers, as the matcher wants them.

    Every field is optional because every question offers "prefer not to say", and
    §6 is explicit that declining must widen results.
    """

    gender: Gender | None = None
    age_band: AgeBand | None = None
    state: str | None = None
    area: Area | None = None
    income_band: IncomeBand | None = None
    caste_category: CasteCategory | None = None
    needs: tuple[NeedCategory, ...] = ()

    @property
    def is_answered(self) -> bool:
        """Enough to match on at all. A need is what makes ranking meaningful."""
        return bool(self.needs)

    @classmethod
    def from_row(cls, profile) -> SchemeProfile:
        """Build from the `profiles` table row, treating "prefer not to say" as unset.

        The enum member exists so the *user* can choose it explicitly; by the time it
        reaches the matcher it must be indistinguishable from an unanswered question,
        or declining would narrow rather than widen.
        """

        def unset_if_declined(value, declined):
            return None if value is None or value == declined else value

        return cls(
            gender=unset_if_declined(profile.gender, Gender.PREFER_NOT_TO_SAY),
            age_band=unset_if_declined(profile.age_band, AgeBand.PREFER_NOT_TO_SAY),
            state=unset_if_declined(profile.state, "prefer_not_to_say"),
            area=unset_if_declined(profile.area, Area.PREFER_NOT_TO_SAY),
            income_band=unset_if_declined(profile.income_band, IncomeBand.PREFER_NOT_TO_SAY),
            caste_category=unset_if_declined(
                profile.category, CasteCategory.PREFER_NOT_TO_SAY
            ),
            needs=tuple(NeedCategory(need) for need in (profile.needs or [])),
        )


@lru_cache
def load_schemes(path: Path | None = None) -> tuple[Scheme, ...]:
    """Read and validate the dataset. A malformed row fails loudly, at startup.

    Cached — call `load_schemes.cache_clear()` after editing the file.
    """
    source = path or SCHEMES_PATH
    if not source.exists():
        return ()

    document = json.loads(source.read_text(encoding="utf-8"))
    rows = document["schemes"] if isinstance(document, dict) else document
    schemes = tuple(Scheme.model_validate(row) for row in rows)

    seen: set[str] = set()
    for scheme in schemes:
        if scheme.id in seen:
            raise ValueError(f"duplicate scheme id in {source.name}: {scheme.id}")
        seen.add(scheme.id)
    return schemes
