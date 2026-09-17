"""Cost accounting and the two caps that stop a demo becoming a bill.

Both caps are checked *before* the call, not after. Discovering the monthly cap was
exceeded once the tokens are spent is not a cap, it is a receipt.

The per-user daily cap is abuse control: one person cannot burn the shared quota
that everyone else in the demo is relying on. The monthly rupee cap is the hard
stop. When it is reached the features say so in plain language and keep working
wherever they can without a model — scam detection still runs its 14 rules, which
catch most of what arrives, so the product degrades rather than dies.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm.base import LLMProvider, SpendCapReached, Usage
from app.models import repo
from app.models.db import User
from app.models.enums import Feature

__all__ = [
    "DailyCapReached",
    "check_caps",
    "estimate_cost_inr",
    "record_call",
    "remaining_budget_inr",
]

log = logging.getLogger(__name__)

PER_MILLION = 1_000_000


class DailyCapReached(SpendCapReached):
    """This user has had their share for today. Everyone else is unaffected."""


def estimate_cost_inr(provider: LLMProvider, usage: Usage, settings: Settings) -> float:
    """Rupees for one call, from published per-token pricing.

    An estimate on purpose. Its only job is to decide when to stop, and a figure
    that is right to the paisa would not change that decision.
    """
    if provider.name == "mock":
        return 0.0
    in_rate = settings.gemini_input_inr_per_mtok
    out_rate = settings.gemini_output_inr_per_mtok
    return (usage.tokens_in * in_rate + usage.tokens_out * out_rate) / PER_MILLION


def remaining_budget_inr(session: Session, settings: Settings | None = None) -> float:
    settings = settings or get_settings()
    return max(0.0, settings.monthly_spend_cap_inr - repo.spend_this_month(session))


def check_caps(session: Session, user: User | None, settings: Settings | None = None) -> None:
    """Raise if this call must not happen. Silent if it may.

    Order matters: the monthly cap is checked first because it affects everyone,
    and telling one user "you have used your 20 for today" when the real problem is
    that the project is out of money would send them back to try again tomorrow.
    """
    settings = settings or get_settings()

    spent = repo.spend_this_month(session)
    if spent >= settings.monthly_spend_cap_inr:
        raise SpendCapReached(
            f"monthly cap of ₹{settings.monthly_spend_cap_inr} reached (₹{spent:.2f} spent)"
        )

    if user is not None:
        used = repo.analyses_today(session, user)
        if used >= settings.daily_analyses_per_user:
            raise DailyCapReached(
                f"user has used {used} of {settings.daily_analyses_per_user} analyses today"
            )


def record_call(
    session: Session,
    provider: LLMProvider,
    *,
    method: str,
    feature: Feature | None = None,
    settings: Settings | None = None,
) -> float:
    """Write the ledger row for the call that just happened. Returns the cost.

    Reads `provider.last_usage()`, so it must be called immediately after the
    provider call it is accounting for.
    """
    settings = settings or get_settings()
    usage = provider.last_usage()
    cost = estimate_cost_inr(provider, usage, settings)

    repo.record_llm_call(
        session,
        provider=provider.name,
        model=provider.model or provider.name,
        method=method,
        feature=feature,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_inr=cost,
    )
    return cost
