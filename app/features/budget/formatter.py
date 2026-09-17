"""The §7 response: chart, category flags, advice, and a goal with a date on it.

The chart is the first thing in the message because it is the thing a low-literacy
user reads fastest — a green slice that is nearly half the circle communicates more
than any sentence below it. Everything after the chart exists to explain it.
"""

from __future__ import annotations

from app.channels.base import Block, Button, Keyboard, OutboundImage, OutboundMessage
from app.features.budget.analyser import BudgetAnalysis
from app.features.budget.goals import GoalKind, SavingsPlan
from app.features.budget.schema import CategoryBand
from app.utils.money import format_inr

__all__ = ["DISCLAIMER", "format_analysis", "format_needs_more"]

DISCLAIMER = (
    "Educational guidance, not financial advice. "
    "These are your numbers, not a plan sold to you."
)

_BAND_MARK: dict[CategoryBand, str] = {
    CategoryBand.UNDER: "⚠️",
    CategoryBand.HEALTHY: "✅",
    CategoryBand.WATCH: "🟡",
    CategoryBand.DANGER: "🔴",
}


def format_analysis(
    analysis: BudgetAnalysis,
    plan: SavingsPlan,
    chart_png: bytes | None = None,
) -> OutboundMessage:
    """The whole §7 layout."""
    budget = analysis.budget
    blocks: list[Block] = [Block.heading(f"📊 Your month — {format_inr(budget.income)}")]

    blocks.append(
        Block.key_values(
            tuple(
                (
                    f"{v.emoji} {v.label}",
                    f"{format_inr(v.amount)} — {v.share_pct:.0f}% "
                    f"{_BAND_MARK[v.band]} {v.band.label}",
                )
                for v in analysis.verdicts
            )
        )
    )

    left_over_mark = _BAND_MARK[analysis.savings_band]
    if budget.in_deficit:
        blocks.append(
            Block.key_values(
                (("🔴 Short by", f"{format_inr(abs(budget.surplus))} a month"),)
            )
        )
    else:
        blocks.append(
            Block.key_values(
                (
                    (
                        "💰 Left over",
                        f"{format_inr(budget.surplus)} — {budget.surplus_pct:.0f}% "
                        f"{left_over_mark} {analysis.savings_band.label}",
                    ),
                )
            )
        )

    blocks.append(Block.divider())
    blocks.append(Block.heading("💡 Advice"))
    blocks.append(Block.para(analysis.headline))
    blocks.append(Block.steps(analysis.advice))

    blocks.extend(_goal_blocks(plan))
    blocks.append(Block.disclaimer(DISCLAIMER))

    images = ()
    if chart_png:
        images = (
            OutboundImage(
                png=chart_png,
                caption=f"Your month — {format_inr(budget.income)}",
                filename="budget.png",
            ),
        )

    return OutboundMessage.of(*blocks, images=images, keyboard=_keyboard())


def _goal_blocks(plan: SavingsPlan) -> list[Block]:
    if not plan.has_plan:
        return []

    blocks = [Block.heading("🎯 Your savings goal")]
    goal = plan.goal or plan.challenge

    if goal.kind is GoalKind.DAILY_CHALLENGE:
        blocks.append(
            Block.key_values(
                (
                    (goal.name, f"{format_inr(goal.target)} in a year"),
                    ("Starting from today", f"by {goal.date_label}"),
                )
            )
        )
        blocks.append(Block.para(goal.why))
        return blocks

    blocks.append(
        Block.key_values(
            (
                (goal.name, format_inr(goal.target)),
                (
                    f"At {format_inr(goal.monthly_saving)}/month",
                    f"{goal.months_label} — by {goal.date_label}",
                ),
            )
        )
    )
    if goal.why:
        blocks.append(Block.para(goal.why))

    if plan.later:
        blocks.append(
            Block.para(
                "After that: " + ", ".join(f"{g.name} ({format_inr(g.target)})" for g in plan.later)
            )
        )

    if plan.safe_parking:
        name, why = plan.safe_parking[0]
        blocks.append(Block.para(f"Where to keep it: {name} — {why}."))

    return blocks


def format_needs_more(reason: str, examples: bool = True) -> OutboundMessage:
    """Parsing was ambiguous. §8: never a naked error, always a next action."""
    blocks = [
        Block.para(f"{reason} Let us do it the easy way instead."),
        Block.para(
            "Tell me your monthly income first — just the number. Then I will ask about "
            "each expense one at a time, and you can say “skip” for anything that does "
            "not apply."
        ),
    ]
    if examples:
        blocks.append(Block.para("Or type it all in one line, like this:"))
        blocks.append(
            Block.bullets(
                (
                    "income 25000 rent 8000 food 4000 travel 3000",
                    "kamai 18k kiraya 6k khana 5000 emi 3000",
                )
            )
        )
    blocks.append(Block.disclaimer(DISCLAIMER))
    return OutboundMessage.of(*blocks, keyboard=_keyboard())


def _keyboard() -> Keyboard:
    return Keyboard.of(
        [Button(label="💰 Redo my month", action="budget")],
        [Button(label="🚩 Check a message", action="scam_check")],
    )
