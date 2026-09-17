"""The savings goal engine. Every goal carries a date.

§7 is blunt about why: *"Save more" is advice nobody acts on. "You will have ₹45,000
by 15 December 2026 if you keep this up" is a commitment device.* A date turns an
intention into something the user can be held to — by themselves — and it moves
earlier when they cut a category, which is the feedback loop the whole feature wants.

The ladder is ordered so the first rung is winnable. ₹5,000 is reachable in weeks on
almost any surplus; a three-month emergency fund is not, and leading with it teaches
people that saving is for other people.

When the surplus is too small for any of that to be honest, the ₹10/day challenge
from slide 13 is offered instead — because a plan that says "you will reach ₹45,000
in 90 months" is arithmetic, not advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.features.budget.schema import Budget, load_benchmarks
from app.utils.money import format_inr

__all__ = ["Goal", "GoalKind", "SavingsPlan", "build_plan"]

DAYS_PER_MONTH = 30.44  # mean Gregorian month; good enough to name a date


class GoalKind(StrEnum):
    BUFFER = "buffer"
    EMERGENCY_FUND = "emergency_fund"
    OWN_GOAL = "own_goal"
    DAILY_CHALLENGE = "daily_challenge"


@dataclass(frozen=True, slots=True)
class Goal:
    kind: GoalKind
    name: str
    target: float
    monthly_saving: float
    months: float
    target_date: date
    why: str = ""

    @property
    def months_label(self) -> str:
        """"4.5 months", "1 month", "under a month" — never "0.6 months"."""
        if self.months < 1:
            return "under a month"
        if abs(self.months - round(self.months)) < 0.05:
            whole = round(self.months)
            return "1 month" if whole == 1 else f"{whole} months"
        return f"{self.months:.1f} months"

    @property
    def date_label(self) -> str:
        return self.target_date.strftime("%d %B %Y")


@dataclass(frozen=True, slots=True)
class SavingsPlan:
    """The next rung, plus the rungs after it, plus where to actually put the money."""

    goal: Goal | None
    later: tuple[Goal, ...] = ()
    challenge: Goal | None = None
    safe_parking: tuple[tuple[str, str], ...] = ()

    @property
    def has_plan(self) -> bool:
        return self.goal is not None or self.challenge is not None


def _months_to(target: float, monthly: float) -> float:
    return target / monthly if monthly > 0 else float("inf")


def _date_after(months: float, today: date) -> date:
    return today + timedelta(days=round(months * DAYS_PER_MONTH))


def build_plan(budget: Budget, today: date | None = None) -> SavingsPlan:
    """Work out the next goal worth naming, and when it lands.

    A deficit gets no savings goal at all — telling someone who is ₹2,500 short each
    month to build an emergency fund is not advice, and the response says so
    elsewhere. The challenge is offered instead, because ₹10 a day is achievable
    while ₹5,000 a month is not.
    """
    today = today or date.today()
    benchmarks = load_benchmarks()
    config = benchmarks.goals
    surplus = budget.surplus

    parking = tuple((item["name"], item["why"]) for item in benchmarks.safe_parking)

    if surplus < config["minimum_credible_surplus"]:
        return SavingsPlan(
            goal=None,
            challenge=_daily_challenge(config, today),
            safe_parking=parking,
        )

    ladder = _ladder(budget, config, surplus, today)
    return SavingsPlan(
        goal=ladder[0] if ladder else None,
        later=tuple(ladder[1:]),
        safe_parking=parking,
    )


def _ladder(budget: Budget, config: dict, surplus: float, today: date) -> list[Goal]:
    """Buffer → emergency fund → the user's own goal, in that order."""
    goals: list[Goal] = []

    buffer_target = float(config["buffer_amount"])
    # Skip the buffer once a single month's surplus already clears it — naming a goal
    # the user reaches this month is patronising, not motivating.
    if surplus < buffer_target:
        months = _months_to(buffer_target, surplus)
        goals.append(
            Goal(
                kind=GoalKind.BUFFER,
                name="First buffer",
                target=buffer_target,
                monthly_saving=surplus,
                months=months,
                target_date=_date_after(months, today),
                why=(
                    "A small cushion so the next unexpected ₹2,000 does not become a loan."
                ),
            )
        )

    emergency_target = budget.total_spent * config["emergency_fund_months"]
    if emergency_target > 0:
        months = _months_to(emergency_target, surplus)
        goals.append(
            Goal(
                kind=GoalKind.EMERGENCY_FUND,
                name="Emergency fund",
                target=emergency_target,
                monthly_saving=surplus,
                months=months,
                target_date=_date_after(months, today),
                why=(
                    f"{config['emergency_fund_months']} months of your expenses. This is the "
                    "money that means a hospital bill or a lost job never sends you to a "
                    "moneylender at 40% interest."
                ),
            )
        )

    if budget.goal_amount and budget.goal_amount > 0:
        months = _months_to(budget.goal_amount, surplus)
        goals.append(
            Goal(
                kind=GoalKind.OWN_GOAL,
                name=budget.goal_name or "Your goal",
                target=float(budget.goal_amount),
                monthly_saving=surplus,
                months=months,
                target_date=_date_after(months, today),
            )
        )

    return goals


def _daily_challenge(config: dict, today: date) -> Goal:
    """Slide 13's entry rung, for a surplus near zero.

    Framed as one year's accumulation, not as months-to-₹5,000. At ₹10 a day the
    buffer is 16 months away, and leading with that number argues against the habit
    the challenge exists to start. "₹3,650 by this date next year" is the same
    arithmetic pointed the right way.
    """
    per_day = float(config["daily_challenge_rupees"])
    monthly = per_day * DAYS_PER_MONTH
    target = per_day * 365
    return Goal(
        kind=GoalKind.DAILY_CHALLENGE,
        name=f"The {format_inr(per_day)}-a-day challenge",
        target=target,
        monthly_saving=monthly,
        months=12.0,
        target_date=date(today.year + 1, today.month, today.day)
        if (today.month, today.day) != (2, 29)
        else date(today.year + 1, 3, 1),
        why=(
            f"{format_inr(per_day)} a day is one cup of tea. Put it in a tin, a jar or a "
            f"separate account — anywhere that is not your main one. That is "
            f"{format_inr(monthly)} a month without changing anything else, and "
            f"{format_inr(target)} in a year."
        ),
    )


def saving_if_reduced(budget: Budget, category: str, by_amount: float, today: date | None = None):
    """How much earlier the current goal lands if one category is cut.

    §7's feedback loop: *"if they cut travel by ₹1,000, the app shows the date moving
    earlier."* Returns `(new_goal, days_earlier)` or `None` when there is no goal to
    move.
    """
    today = today or date.today()
    current = build_plan(budget, today).goal
    if current is None:
        return None

    reduced = dict(budget.categories)
    reduced[category] = max(0.0, reduced.get(category, 0.0) - by_amount)
    trimmed = Budget(
        income=budget.income,
        categories=reduced,
        month=budget.month,
        goal_name=budget.goal_name,
        goal_amount=budget.goal_amount,
    )

    improved = build_plan(trimmed, today).goal
    if improved is None or improved.kind is not current.kind:
        return None
    return improved, (current.target_date - improved.target_date).days
