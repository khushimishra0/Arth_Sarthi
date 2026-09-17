"""The handful of database operations the channels and features actually perform.

Kept apart from `db.py` so the table definitions stay readable as a description of
what is stored, and so a feature never has to hand-write a query — and therefore
never has to remember, at three in the morning before a demo, that a user is
identified by (channel, channel_user_id) and not by the Telegram id alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import Analysis, Budget, LlmCall, Profile, User, utcnow
from app.models.enums import Channel, Feature, Language

__all__ = [
    "analyses_today",
    "cached_analysis",
    "get_or_create_profile",
    "get_or_create_user",
    "record_analysis",
    "record_llm_call",
    "set_language",
    "spend_this_month",
    "upsert_budget",
]


def get_or_create_user(session: Session, *, channel: Channel, channel_user_id: str) -> User:
    """Find the user behind a channel id, creating the record on first contact.

    Called before anything else in every handler. No form, no onboarding — §8's
    "sets up the user record; no forms".
    """
    user = session.scalar(
        select(User).where(User.channel == channel, User.channel_user_id == str(channel_user_id))
    )
    if user is None:
        user = User(channel=channel, channel_user_id=str(channel_user_id))
        session.add(user)
        session.commit()
    return user


def set_language(session: Session, user: User, language: Language) -> User:
    user.language = language
    session.commit()
    return user


def get_or_create_profile(session: Session, user: User) -> Profile:
    profile = session.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id, needs=[])
        session.add(profile)
        session.commit()
    return profile


def record_analysis(
    session: Session,
    user: User,
    *,
    feature: Feature,
    input_hash: str,
    result: dict[str, Any],
) -> Analysis:
    analysis = Analysis(
        user_id=user.id, feature=feature, input_hash=input_hash, result_json=result
    )
    session.add(analysis)
    session.commit()
    return analysis


def cached_analysis(
    session: Session,
    *,
    feature: Feature,
    input_hash: str,
    max_age: timedelta = timedelta(days=7),
    now: datetime | None = None,
) -> Analysis | None:
    """Most recent analysis of this exact input, if it is still fresh.

    Deliberately not scoped to one user: the whole point is that the screenshot
    doing the rounds of a WhatsApp group is analysed once for everyone who sends it.
    Nothing user-specific is stored in a result, so this leaks nothing between users.
    """
    cutoff = (now or utcnow()) - max_age
    return session.scalar(
        select(Analysis)
        .where(
            Analysis.feature == feature,
            Analysis.input_hash == input_hash,
            Analysis.created_at >= cutoff,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )


def analyses_today(session: Session, user: User, *, now: datetime | None = None) -> int:
    """Count towards the per-user daily cap in config."""
    moment = now or utcnow()
    start_of_day = datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
    return int(
        session.scalar(
            select(func.count())
            .select_from(Analysis)
            .where(Analysis.user_id == user.id, Analysis.created_at >= start_of_day)
        )
        or 0
    )


def record_llm_call(
    session: Session,
    *,
    provider: str,
    model: str,
    method: str,
    tokens_in: int,
    tokens_out: int,
    cost_inr: float,
    feature: Feature | None = None,
) -> LlmCall:
    """Log a billable call. Written after the call, whether or not it succeeded usefully."""
    call = LlmCall(
        provider=provider,
        model=model,
        method=method,
        feature=feature,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_inr=cost_inr,
    )
    session.add(call)
    session.commit()
    return call


def spend_this_month(session: Session, *, now: datetime | None = None) -> float:
    """Estimated rupees spent since the 1st. Compared against MONTHLY_SPEND_CAP_INR."""
    moment = now or utcnow()
    start_of_month = datetime(moment.year, moment.month, 1, tzinfo=UTC)
    return float(
        session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_inr), 0.0)).where(
                LlmCall.created_at >= start_of_month
            )
        )
        or 0.0
    )


def upsert_budget(
    session: Session,
    user: User,
    *,
    month: str,
    income: float,
    categories: dict[str, float],
    savings_goal: float | None = None,
    target_date: date | None = None,
) -> Budget:
    """One budget per user per month — re-entering a month replaces it.

    Users correct themselves ("no wait, rent is 8500"). Appending a second row for
    the same month would make month-on-month tracking count them twice.
    """
    budget = session.scalar(
        select(Budget).where(Budget.user_id == user.id, Budget.month == month)
    )
    if budget is None:
        budget = Budget(user_id=user.id, month=month, income=income, categories_json=categories)
        session.add(budget)
    else:
        budget.income = income
        budget.categories_json = categories
    budget.savings_goal = savings_goal
    budget.target_date = target_date
    session.commit()
    return budget
