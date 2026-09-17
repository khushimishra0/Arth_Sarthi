"""Ranked scheme cards, with an apply link and a verification date on every one.

The verification date is not decoration. §6: *"The demo displays the verification
date... Before any real-user deployment the whole dataset must be re-verified —
scheme ceilings and names change with every budget."* A user who sees "checked on 3
August 2026" can judge for themselves how much to trust it, and a stale dataset
becomes visible instead of silently wrong.

The first card is labelled "Best match" only when the purpose term actually fired —
when the scheme is for what the user said they needed. Labelling whatever happens to
sort first would make the label meaningless.
"""

from __future__ import annotations

from datetime import date

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.features.schemes.ranker import Ranked
from app.features.schemes.schema import SchemeProfile
from app.models.enums import income_band_annual_range
from app.utils.money import in_words

__all__ = ["DISCLAIMER", "format_matches", "format_no_matches", "format_unavailable"]

DISCLAIMER = (
    "Scheme details are from our verified database, not from memory. "
    "Confirm current terms on the official portal before applying — limits change "
    "with every budget."
)

_CATEGORY_EMOJI: dict[str, str] = {
    "education": "🎓",
    "business": "💼",
    "housing": "🏠",
    "health": "🏥",
    "pension": "👵",
    "farming": "🌾",
    "savings": "🏦",
}


def _profile_line(profile: SchemeProfile) -> str:
    """"woman · village · ₹1.44 lakh/year · education" — §6's own summary line."""
    parts: list[str] = []

    if profile.gender is not None:
        parts.append({"female": "woman", "male": "man"}.get(profile.gender.value, "person"))
    if profile.area is not None:
        parts.append({"rural": "village", "urban": "town or city"}.get(profile.area.value, ""))
    if profile.income_band is not None:
        low, high = income_band_annual_range(profile.income_band)
        parts.append(
            f"{in_words(low)}–{in_words(high)}/year household income"
            if high is not None
            else f"over {in_words(low)}/year household income"
        )
    if profile.needs:
        parts.append(" and ".join(need.value for need in profile.needs))

    return " · ".join(part for part in parts if part)


def format_matches(
    ranked: tuple[Ranked, ...], profile: SchemeProfile, explanations: dict
) -> OutboundMessage:
    """The ranked cards. `explanations` is `{scheme_id: text}` from the explainer."""
    if not ranked:
        return format_no_matches(profile)

    count = len(ranked)
    lead = _CATEGORY_EMOJI.get(ranked[0].scheme.category, "🏛️")
    blocks: list[Block] = [
        Block.heading(
            f"{lead} You may qualify for {count} scheme" + ("s" if count != 1 else "")
        ),
        Block.para(f"Based on: {_profile_line(profile)}"),
    ]

    for position, item in enumerate(ranked, start=1):
        scheme = item.scheme
        blocks.append(Block.divider())

        label = f"{position}. {scheme.name}"
        if position == 1 and item.is_best_match:
            label += "  —  Best match"
        blocks.append(Block.heading(label))

        blocks.append(Block.para(explanations.get(scheme.id, scheme.benefit_text)))

        if scheme.eligibility.conditions:
            blocks.append(Block.bullets(tuple(f"⚠️ {c}" for c in scheme.eligibility.conditions)))

        pairs: list[tuple[str, str]] = []
        if scheme.documents_required:
            pairs.append(("You will need", ", ".join(scheme.documents_required)))
        if scheme.how_to_apply:
            pairs.append(("How to apply", scheme.how_to_apply))
        if scheme.helpline:
            pairs.append(("Helpline", scheme.helpline))
        pairs.append(("Details checked", _pretty_date(scheme.last_verified)))
        blocks.append(Block.key_values(tuple(pairs)))

    blocks.append(Block.divider())
    blocks.append(Block.disclaimer(DISCLAIMER))

    return OutboundMessage.of(*blocks, keyboard=_keyboard(ranked))


def format_no_matches(profile: SchemeProfile) -> OutboundMessage:
    """Nothing matched. §8: never a dead end.

    Says plainly that our database is small rather than implying the user qualifies
    for nothing — those are very different statements and only one of them is true.
    """
    return OutboundMessage.of(
        Block.heading("I could not match you to a scheme in my database"),
        Block.para(
            "That does not mean nothing exists for you. My database holds only the "
            "schemes I have verified myself, and it is still small — there are hundreds "
            "of central and state schemes I have not checked yet."
        ),
        Block.para(
            "The official government portal lists all of them and lets you filter by "
            "the same details you just gave me."
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=Keyboard.of(
            [Button(label="🏛️ Search the official portal", url="https://www.myscheme.gov.in/")],
            [Button(label="Change my answers", action="scheme:restart")],
        ),
    )


def format_unavailable() -> OutboundMessage:
    """The dataset is missing or empty — a deployment fault, not a user one."""
    return OutboundMessage.of(
        Block.para(
            "My scheme database is not loaded right now, so I will not guess at what you "
            "might qualify for. That is a problem on my side and someone has been told."
        ),
        Block.para("The official portal has the full list and works the same way:"),
        Block.disclaimer(DISCLAIMER),
        keyboard=Keyboard.of(
            [Button(label="🏛️ Search the official portal", url="https://www.myscheme.gov.in/")]
        ),
    )


def _pretty_date(value: date) -> str:
    return value.strftime("%d %B %Y")


def _keyboard(ranked: tuple[Ranked, ...]) -> Keyboard:
    """Apply links for the top schemes, then a way to change the answers."""
    rows: list[list[Button]] = []
    for item in ranked[:3]:
        if item.scheme.application_url:
            rows.append(
                [
                    Button(
                        label=f"Apply · {_shorten(item.scheme.name)}",
                        url=item.scheme.application_url,
                    )
                ]
            )
    rows.append([Button(label="Change my answers", action="scheme:restart")])
    return Keyboard.of(*rows)


def _shorten(name: str, limit: int = 28) -> str:
    """Telegram truncates long button labels, so do it deliberately."""
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "…"
