"""Loan finance engine — pure Python. No AI, no I/O, no network.

This module is the reason the product can be trusted. A language model extracts
"processing fee: 2.5%" from a sanction letter; *this file* works out what 2.5%
actually costs over 36 months. Every rupee figure a user ever sees originates here.

Nothing in here may import an AI SDK, read a file, or hit a database. If this module
is wrong, every other part of the loan feature is confidently wrong — which is worse
than the feature being absent. It is therefore tested standalone, against amortisation
schedules verified by hand, before any PDF is parsed.

Rate conventions
----------------
Rates are passed and returned as *annual percentages* (14.0 means 14% p.a.).
Internally everything works on the monthly rate, annual / 12.

"Annual rate" here means the **nominal** annual rate (monthly rate x 12), which is how
Indian lenders quote. `effective_annual_rate()` converts to the compounded figure when
a genuine annual-equivalent is wanted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "MONTHS_PER_YEAR",
    "Installment",
    "PenaltyExposure",
    "PrepaymentCost",
    "amortisation_schedule",
    "effective_annual_rate",
    "emi",
    "emi_flat",
    "flat_to_reducing",
    "outstanding_balance",
    "penalty_exposure",
    "prepayment_cost",
    "rate_from_emi",
    "total_repayment",
    "true_apr",
]

MONTHS_PER_YEAR = 12

# Solver bounds, as a monthly rate. 0 to 100%/month (0-1200% p.a.) comfortably
# brackets anything a real document can contain, including moneylender territory.
_RATE_SOLVER_LO = 0.0
_RATE_SOLVER_HI = 1.0
_RATE_SOLVER_TOL = 1e-12
_RATE_SOLVER_MAX_ITER = 200


def _validate(principal: float, months: int) -> None:
    if principal <= 0:
        raise ValueError(f"principal must be positive, got {principal}")
    if months <= 0:
        raise ValueError(f"months must be positive, got {months}")


def _monthly(annual_rate_pct: float) -> float:
    return annual_rate_pct / 100.0 / MONTHS_PER_YEAR


# ---------------------------------------------------------------------------
# EMI
# ---------------------------------------------------------------------------


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    """Equated monthly instalment on a reducing-balance loan.

        EMI = P.r.(1+r)^n / ((1+r)^n - 1)

    A zero rate degrades to simple division rather than dividing by zero.
    """
    _validate(principal, months)
    if annual_rate_pct < 0:
        raise ValueError(f"annual_rate_pct must not be negative, got {annual_rate_pct}")

    r = _monthly(annual_rate_pct)
    if r == 0:
        return principal / months

    growth = (1 + r) ** months
    return principal * r * growth / (growth - 1)


def emi_flat(principal: float, flat_rate_pct: float, months: int) -> float:
    """EMI under a *flat* rate: interest charged on the full principal throughout.

    Total interest = P x rate x years, regardless of how much has been repaid.
    This is the convention behind most "12% interest" microfinance and vehicle
    loans in India, and it is roughly 1.8x as expensive as it sounds.
    """
    _validate(principal, months)
    if flat_rate_pct < 0:
        raise ValueError(f"flat_rate_pct must not be negative, got {flat_rate_pct}")

    years = months / MONTHS_PER_YEAR
    total_interest = principal * (flat_rate_pct / 100.0) * years
    return (principal + total_interest) / months


# ---------------------------------------------------------------------------
# Rate recovery — the solver both flat->reducing and true APR are built on
# ---------------------------------------------------------------------------


def rate_from_emi(principal: float, instalment: float, months: int) -> float:
    """Annual reducing-balance rate implied by a known principal, EMI and tenure.

    Bisection, not Newton: EMI is monotonically increasing in the rate, so bisection
    is guaranteed to converge and cannot diverge on a pathological document. Slower
    by microseconds, correct always.

    An instalment at or below principal/months implies a zero or negative rate; that
    is returned as 0.0 rather than raising, since a genuinely interest-free loan is a
    real thing (and a below-cost instalment means the document contradicts itself,
    which the hidden-charge detector reports separately).
    """
    _validate(principal, months)
    if instalment <= 0:
        raise ValueError(f"instalment must be positive, got {instalment}")

    if instalment <= principal / months:
        return 0.0

    lo, hi = _RATE_SOLVER_LO, _RATE_SOLVER_HI
    if emi(principal, hi * MONTHS_PER_YEAR * 100, months) < instalment:
        raise ValueError(
            f"instalment {instalment} implies a rate above "
            f"{hi * MONTHS_PER_YEAR * 100:.0f}% p.a.; refusing to guess"
        )

    for _ in range(_RATE_SOLVER_MAX_ITER):
        mid = (lo + hi) / 2
        if emi(principal, mid * MONTHS_PER_YEAR * 100, months) < instalment:
            lo = mid
        else:
            hi = mid
        if hi - lo < _RATE_SOLVER_TOL:
            break

    return (lo + hi) / 2 * MONTHS_PER_YEAR * 100


def flat_to_reducing(principal: float, flat_rate_pct: float, months: int) -> float:
    """Convert a flat rate to the reducing-balance rate that costs the same.

    The single biggest deception in Indian lending. A borrower compares a "12% flat"
    offer against a bank's 14% reducing and picks the worse loan. This function is
    what lets the product say "they said 14%, it is really 24.9%".
    """
    return rate_from_emi(principal, emi_flat(principal, flat_rate_pct, months), months)


def effective_annual_rate(nominal_annual_pct: float) -> float:
    """Nominal annual rate -> effective annual rate, compounding monthly."""
    r = _monthly(nominal_annual_pct)
    return ((1 + r) ** MONTHS_PER_YEAR - 1) * 100


# ---------------------------------------------------------------------------
# True cost
# ---------------------------------------------------------------------------


def true_apr(net_disbursed: float, instalment: float, months: int) -> float:
    """The only number that lets two loan offers be compared honestly.

    IRR over the actual cash flows: what the borrower really received (principal
    minus fees, GST, and bundled insurance deducted at source) against the EMI
    stream they really pay.

    A loan that hands over Rs 97,050 and collects EMIs sized for Rs 1,00,000 costs
    more than its headline rate, and this is where that shows up.
    """
    return rate_from_emi(net_disbursed, instalment, months)


def total_repayment(instalment: float, months: int, one_time_charges: float = 0.0) -> float:
    """EMI x tenure, plus every one-time charge.

    "You borrow Rs 1,00,000 and repay Rs 1,47,600" is the sentence that actually
    changes behaviour — more than any percentage.
    """
    if months <= 0:
        raise ValueError(f"months must be positive, got {months}")
    return instalment * months + one_time_charges


# ---------------------------------------------------------------------------
# Amortisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Installment:
    month: int
    opening_balance: float
    payment: float
    interest: float
    principal: float
    closing_balance: float


def amortisation_schedule(
    principal: float, annual_rate_pct: float, months: int
) -> list[Installment]:
    """Month-by-month breakdown of a reducing-balance loan.

    Used to answer "how much do I still owe in month 8" for prepayment maths, and to
    back the "ask them to show you the amortisation schedule" negotiation point.

    The final instalment absorbs accumulated rounding so the closing balance lands
    exactly at zero rather than a few paise either side.
    """
    _validate(principal, months)
    r = _monthly(annual_rate_pct)
    payment = emi(principal, annual_rate_pct, months)

    schedule: list[Installment] = []
    balance = principal

    for month in range(1, months + 1):
        interest = balance * r
        if month == months:
            principal_part = balance
            actual_payment = balance + interest
        else:
            principal_part = payment - interest
            actual_payment = payment
        closing = balance - principal_part
        schedule.append(
            Installment(
                month=month,
                opening_balance=balance,
                payment=actual_payment,
                interest=interest,
                principal=principal_part,
                closing_balance=max(closing, 0.0),
            )
        )
        balance = closing

    return schedule


def outstanding_balance(
    principal: float, annual_rate_pct: float, months: int, after_months: int
) -> float:
    """Balance still owed after `after_months` instalments have been paid."""
    if after_months < 0:
        raise ValueError(f"after_months must not be negative, got {after_months}")
    if after_months == 0:
        return principal
    if after_months >= months:
        return 0.0
    return amortisation_schedule(principal, annual_rate_pct, months)[
        after_months - 1
    ].closing_balance


# ---------------------------------------------------------------------------
# What going wrong costs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PenaltyExposure:
    """What one missed EMI actually costs.

    Turns "2% per month on overdue" — which means nothing to a borrower — into
    "one missed EMI costs you Rs 607".
    """

    overdue_amount: float
    penalty_interest: float
    bounce_charge: float
    extra_loan_interest: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.penalty_interest + self.bounce_charge + self.extra_loan_interest


def penalty_exposure(
    instalment: float,
    penalty_pct_per_month: float,
    bounce_charge: float = 0.0,
    months_overdue: int = 1,
    loan_annual_rate_pct: float | None = None,
) -> PenaltyExposure:
    """Cost of missing `months_overdue` instalments.

    `loan_annual_rate_pct` is optional and off by default. When supplied it adds the
    interest-on-interest the lender also charges on the unpaid amount — real, but it
    makes the headline figure harder to verify against the document, so the caller
    opts in.
    """
    if instalment <= 0:
        raise ValueError(f"instalment must be positive, got {instalment}")
    if months_overdue <= 0:
        raise ValueError(f"months_overdue must be positive, got {months_overdue}")

    overdue = instalment * months_overdue
    penalty = instalment * (penalty_pct_per_month / 100.0) * months_overdue
    extra = 0.0
    if loan_annual_rate_pct is not None:
        extra = overdue * _monthly(loan_annual_rate_pct) * months_overdue

    return PenaltyExposure(
        overdue_amount=overdue,
        penalty_interest=penalty,
        bounce_charge=bounce_charge,
        extra_loan_interest=extra,
        components={
            "penalty_interest": penalty,
            "bounce_charge": bounce_charge,
            "interest_on_overdue": extra,
        },
    )


@dataclass(frozen=True)
class PrepaymentCost:
    """Whether escaping the loan early is even possible, and at what price."""

    allowed: bool
    months_until_allowed: int
    outstanding: float
    charge: float
    reason: str | None = None


def prepayment_cost(
    principal: float,
    annual_rate_pct: float,
    months: int,
    prepay_after_months: int,
    charge_pct: float,
    lock_in_months: int = 0,
) -> PrepaymentCost:
    """Cost of foreclosing after `prepay_after_months` instalments."""
    balance = outstanding_balance(principal, annual_rate_pct, months, prepay_after_months)

    if prepay_after_months < lock_in_months:
        return PrepaymentCost(
            allowed=False,
            months_until_allowed=lock_in_months - prepay_after_months,
            outstanding=balance,
            charge=0.0,
            reason=(
                f"Foreclosure is locked for the first {lock_in_months} months. "
                f"You cannot close this loan for another "
                f"{lock_in_months - prepay_after_months} months."
            ),
        )

    return PrepaymentCost(
        allowed=True,
        months_until_allowed=0,
        outstanding=balance,
        charge=balance * (charge_pct / 100.0),
    )
