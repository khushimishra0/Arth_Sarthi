"""Orchestration. Parse → analyse → chart → goal → format, and persist the month.

The only feature with no LLM call anywhere, so there is no cache, no cap check and
no cost. It still runs the chart off the event loop: matplotlib takes a few hundred
milliseconds and a bot that stops answering everyone else while it draws a pie is a
bot that looks broken.

The month is stored so `/budget` next month can compare — which is the whole reason
`budgets` has a `month` column.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.channels.base import OutboundMessage
from app.features.budget.analyser import BudgetAnalysis, analyse
from app.features.budget.chart import render_chart
from app.features.budget.formatter import format_analysis, format_needs_more
from app.features.budget.goals import SavingsPlan, build_plan
from app.features.budget.parser import parse
from app.features.budget.schema import Budget
from app.models import repo
from app.models.db import User

__all__ = ["analyse_budget", "check_budget_text", "current_month", "run_budget"]

log = logging.getLogger(__name__)


def current_month(today: date | None = None) -> str:
    return (today or date.today()).strftime("%Y-%m")


def analyse_budget(
    budget: Budget, today: date | None = None
) -> tuple[BudgetAnalysis, SavingsPlan]:
    """The pure half: verdicts and a goal. No I/O, no chart, no database."""
    return analyse(budget), build_plan(budget, today)


def run_budget(
    budget: Budget,
    *,
    with_chart: bool = True,
    language: str = "en",
    today: date | None = None,
) -> OutboundMessage:
    """Everything except persistence. Blocking — call it via `asyncio.to_thread`."""
    analysis, plan = analyse_budget(budget, today)
    chart = render_chart(budget, language) if with_chart else None
    return format_analysis(analysis, plan, chart)


async def check_budget_text(
    text: str,
    *,
    session: Session,
    user: User,
    language: str = "en",
    today: date | None = None,
) -> OutboundMessage:
    """A free-text budget line, end to end. Never raises.

    Ambiguity falls back to the guided flow rather than guessing — §7. Acting on a
    half-read line would draw a chart of a month the user does not have, and they
    would have no way to notice.
    """
    result = parse(text)
    if not result.is_confident:
        return format_needs_more(result.ambiguity)

    budget = result.budget
    if not budget.is_usable:
        return format_needs_more("I need your income and at least one expense.")

    month = current_month(today)
    stored = Budget(
        income=budget.income,
        categories=budget.categories,
        month=month,
        goal_name=budget.goal_name,
        goal_amount=budget.goal_amount,
    )

    response = await asyncio.to_thread(
        run_budget, stored, with_chart=True, language=language, today=today
    )

    _, plan = analyse_budget(stored, today)
    goal = plan.goal or plan.challenge
    repo.upsert_budget(
        session,
        user,
        month=month,
        income=stored.income,
        categories=stored.categories,
        savings_goal=goal.target if goal else None,
        target_date=goal.target_date if goal else None,
    )
    return response
