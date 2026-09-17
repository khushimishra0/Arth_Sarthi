"""Turning a budget into verdicts and the three things to do about them.

The advice is generated in code, not by a model. Every sentence is tied to a
threshold that fired, so it can be checked, and it cannot flatter a month that does
not deserve it. §7's tone is the constraint: honest about a 32% rent without nagging
about it, and never congratulatory about food at 12% of income.

Advice is capped at three points on purpose. A list of eight things to fix is a list
nobody starts.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.budget.schema import (
    Budget,
    BudgetBenchmarks,
    CategoryBand,
    CategoryVerdict,
    load_benchmarks,
)
from app.utils.money import format_inr

__all__ = ["BudgetAnalysis", "analyse"]

MAX_ADVICE_POINTS = 3


@dataclass(frozen=True, slots=True)
class BudgetAnalysis:
    budget: Budget
    verdicts: tuple[CategoryVerdict, ...]
    savings_band: CategoryBand
    advice: tuple[str, ...]
    headline: str

    @property
    def concerns(self) -> tuple[CategoryVerdict, ...]:
        return tuple(
            v for v in self.verdicts if v.band in (CategoryBand.WATCH, CategoryBand.DANGER)
        )


def analyse(budget: Budget) -> BudgetAnalysis:
    """Verdict per category, then the three things worth saying."""
    benchmarks = load_benchmarks()
    verdicts = _verdicts(budget, benchmarks)
    savings_band = benchmarks.savings_band(budget.surplus_pct)

    return BudgetAnalysis(
        budget=budget,
        verdicts=verdicts,
        savings_band=savings_band,
        advice=_advice(budget, verdicts, savings_band, benchmarks),
        headline=_headline(budget, savings_band),
    )


def _verdicts(budget: Budget, benchmarks: BudgetBenchmarks) -> tuple[CategoryVerdict, ...]:
    """In the benchmark file's order, so the response reads the same way every time."""
    out: list[CategoryVerdict] = []
    for key, spec in benchmarks.categories.items():
        amount = budget.categories.get(key)
        if amount is None or amount <= 0:
            continue
        share = budget.share_of_income(key)
        out.append(
            CategoryVerdict(
                key=key,
                label=spec["label"],
                emoji=spec.get("emoji", "•"),
                amount=amount,
                share_pct=share,
                band=benchmarks.band_for(key, share),
                note=spec.get("note", ""),
            )
        )
    return tuple(out)


def _headline(budget: Budget, savings_band: CategoryBand) -> str:
    if budget.in_deficit:
        return (
            f"You are spending {format_inr(abs(budget.surplus))} more than you earn each "
            f"month. That gap is being filled by borrowing, savings, or someone you owe."
        )
    if savings_band is CategoryBand.HEALTHY:
        return (
            f"You are saving {budget.surplus_pct:.0f}% of your income. That is genuinely "
            f"rare — most households at this income level save under 10%."
        )
    if savings_band is CategoryBand.WATCH:
        return (
            f"You are saving {budget.surplus_pct:.0f}% — {format_inr(budget.surplus)} a "
            f"month. That is real, and it is worth protecting."
        )
    return (
        f"You have {format_inr(budget.surplus)} left at the end of the month, about "
        f"{budget.surplus_pct:.0f}% of your income. It is tight, but it is not nothing."
    )


def _advice(
    budget: Budget,
    verdicts: tuple[CategoryVerdict, ...],
    savings_band: CategoryBand,
    benchmarks: BudgetBenchmarks,
) -> tuple[str, ...]:
    """At most three, worst-first, each naming a rupee figure from their own month."""
    points: list[str] = []

    if budget.in_deficit:
        points.append(
            f"Find {format_inr(abs(budget.surplus))} a month, starting with the largest "
            f"line you control. Until the month adds up, everything else waits."
        )

    emi = next((v for v in verdicts if v.key == "emi"), None)
    if emi and emi.band is CategoryBand.DANGER:
        points.append(
            f"Your loan EMIs are {emi.share_pct:.0f}% of your income — above the 35% mark "
            f"where debt usually stops being manageable. Do not take another loan to pay "
            f"these. If you are already struggling, talk to the lender about a longer "
            f"tenure before you miss a payment."
        )

    under = next((v for v in verdicts if v.band is CategoryBand.UNDER), None)
    if under:
        points.append(
            f"{under.label} at {under.share_pct:.0f}% of your income is low — "
            f"{format_inr(under.amount)} a month. If that is because money is short "
            f"rather than because food is cheap where you are, that is the first thing "
            f"to fix, not the last."
        )

    # Protecting an existing surplus beats trimming a category that is already fine.
    if savings_band is CategoryBand.HEALTHY and not budget.in_deficit:
        parking = benchmarks.safe_parking[0]
        points.append(
            f"Move it out of your account on payday. Money sitting in a savings account "
            f"gets spent. Set up {parking['name']} for about "
            f"{format_inr(budget.surplus * 0.8)} on the day you are paid — {parking['why']}."
        )

    for verdict in sorted(verdicts, key=lambda v: v.share_pct, reverse=True):
        if len(points) >= MAX_ADVICE_POINTS:
            break
        if verdict.band is CategoryBand.DANGER and verdict.key != "emi":
            points.append(
                f"{verdict.label} is {verdict.share_pct:.0f}% of your income at "
                f"{format_inr(verdict.amount)}. {verdict.note or ''}".strip()
            )
        elif verdict.band is CategoryBand.WATCH and verdict.key == "travel":
            points.append(
                f"Travel at {format_inr(verdict.amount)} is the easiest place to find "
                f"another ₹500–1,000 if you want to reach your goal sooner — a monthly "
                f"pass usually beats daily tickets."
            )
        elif verdict.band is CategoryBand.WATCH and verdict.key == "rent":
            points.append(
                f"Rent at {verdict.share_pct:.0f}% is a little high — not a problem while "
                f"your savings hold up, but it is the line to watch if your income drops."
            )

    if not points:
        points.append(
            "Nothing here needs fixing. Keep doing this and check back next month — "
            "month-on-month is where the pattern shows."
        )

    return tuple(points[:MAX_ADVICE_POINTS])
