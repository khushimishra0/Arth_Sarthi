"""The budget itself, and the thresholds it is judged against.

No AI anywhere in this feature. The user types numbers, Python divides them, and
`data/budget_benchmarks.yaml` decides what the shares mean. That makes the whole of
Phase 5 free to run and free to test — and means a wrong answer here is a bug in a
division, not a model behaving oddly.

`Budget` is deliberately not a Pydantic model over user input: the parser produces
it, and the parser is the thing that validates. Keeping it a plain frozen dataclass
means the chart and the goal engine can be handed one in a test without ceremony.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

__all__ = [
    "BENCHMARKS_PATH",
    "Budget",
    "BudgetBenchmarks",
    "CategoryBand",
    "CategoryVerdict",
    "load_benchmarks",
]

BENCHMARKS_PATH = Path(__file__).resolve().parents[3] / "data" / "budget_benchmarks.yaml"


class CategoryBand(StrEnum):
    """What a share of income means. `UNDER` only applies where spending too little
    is itself a finding — see `food` in the YAML."""

    UNDER = "under"
    HEALTHY = "healthy"
    WATCH = "watch"
    DANGER = "danger"

    @property
    def label(self) -> str:
        return {
            CategoryBand.UNDER: "too low",
            CategoryBand.HEALTHY: "healthy",
            CategoryBand.WATCH: "slightly high",
            CategoryBand.DANGER: "too high",
        }[self]


@dataclass(frozen=True, slots=True)
class Budget:
    """One month of numbers the user typed. Amounts in whole rupees.

    `categories` holds only what was actually given — a skipped category is absent,
    not zero, because "I spend nothing on medicine" and "I did not answer" are
    different facts and only one of them is worth commenting on.
    """

    income: float
    categories: dict[str, float] = field(default_factory=dict)
    month: str = ""
    goal_name: str = ""
    goal_amount: float | None = None

    @property
    def total_spent(self) -> float:
        return sum(self.categories.values())

    @property
    def surplus(self) -> float:
        """What is left. Negative means the month does not add up — a deficit."""
        return self.income - self.total_spent

    @property
    def surplus_pct(self) -> float:
        return 0.0 if self.income <= 0 else self.surplus / self.income * 100.0

    @property
    def in_deficit(self) -> bool:
        return self.surplus < 0

    def share_of_income(self, category: str) -> float:
        if self.income <= 0:
            return 0.0
        return self.categories.get(category, 0.0) / self.income * 100.0

    @property
    def is_usable(self) -> bool:
        """Enough to say anything at all: an income and at least one expense."""
        return self.income > 0 and bool(self.categories)


@dataclass(frozen=True, slots=True)
class CategoryVerdict:
    """One line of the response: what was spent, what share that is, and the call."""

    key: str
    label: str
    emoji: str
    amount: float
    share_pct: float
    band: CategoryBand
    note: str = ""


@dataclass(frozen=True, slots=True)
class BudgetBenchmarks:
    categories: dict
    savings: dict
    goals: dict
    safe_parking: list

    def known_categories(self) -> tuple[str, ...]:
        return tuple(self.categories)

    def band_for(self, key: str, share_pct: float) -> CategoryBand:
        """Place a share against its category's bands."""
        spec = self.categories.get(key)
        if spec is None:
            return CategoryBand.HEALTHY

        under_min = spec.get("under_min")
        if under_min is not None and share_pct < under_min:
            return CategoryBand.UNDER
        if share_pct <= spec["healthy_max"]:
            return CategoryBand.HEALTHY
        if share_pct <= spec["watch_max"]:
            return CategoryBand.WATCH
        return CategoryBand.DANGER

    def savings_band(self, share_pct: float) -> CategoryBand:
        """Inverted: savings is a floor to clear, not a ceiling to stay under."""
        if share_pct >= self.savings["healthy_min"]:
            return CategoryBand.HEALTHY
        if share_pct >= self.savings["watch_min"]:
            return CategoryBand.WATCH
        return CategoryBand.DANGER


@lru_cache
def load_benchmarks(path: Path | None = None) -> BudgetBenchmarks:
    document = yaml.safe_load((path or BENCHMARKS_PATH).read_text(encoding="utf-8"))
    return BudgetBenchmarks(
        categories=document["categories"],
        savings=document["savings"],
        goals=document["goals"],
        safe_parking=document["safe_parking"],
    )
