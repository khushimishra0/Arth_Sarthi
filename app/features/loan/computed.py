"""Everything derived from `LoanTerms`, computed by Phase 1's engine. No AI here.

This is the join between the two halves of the feature: the model read the fields,
and `finance.py` — which was built and proven before any PDF was ever parsed — turns
them into the numbers a borrower actually needs. Nothing in this file generates a
figure; it arranges calls to a tested engine.

The one piece of judgement is `effective_reducing_rate`. A "14% flat" loan and a
"14% reducing" loan are wildly different products, and which one a document means
decides every number below it. When the basis is `undisclosed`, this does **not**
pick one. It computes the reducing rate implied by the stated EMI if there is one,
and otherwise reports that the loan cannot be costed — because assuming "reducing"
would flatter a document that is hiding something, and assuming "flat" would
slander one that simply printed badly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.features.loan.finance import (
    PenaltyExposure,
    PrepaymentCost,
    emi,
    emi_flat,
    flat_to_reducing,
    penalty_exposure,
    prepayment_cost,
    rate_from_emi,
    total_repayment,
    true_apr,
)
from app.features.loan.schema import LoanTerms

__all__ = ["ComputedLoan", "compute"]

# "2% per month", "2 % p.m.", "24% per annum on overdue" — pulled out of the verbatim
# penalty text so `penalty_exposure` can be given a number.
_PENALTY_PATTERNS = (
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:per|p\.?)\s*month", 1.0),
    (r"(\d+(?:\.\d+)?)\s*%\s*p\.?\s*m\.?", 1.0),
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:per|p\.?)\s*(?:annum|year)", 1.0 / 12.0),
    (r"(\d+(?:\.\d+)?)\s*%\s*p\.?\s*a\.?", 1.0 / 12.0),
)


def penalty_pct_per_month(text: str | None) -> float | None:
    """Read a monthly penalty rate out of the document's own words.

    Returns `None` rather than a default when the text cannot be parsed — an
    invented penalty rate would produce an invented "one missed EMI costs you ₹X",
    which is precisely the kind of confident wrongness this product exists to avoid.
    """
    if not text:
        return None
    import re

    for pattern, to_monthly in _PENALTY_PATTERNS:
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            return float(found.group(1)) * to_monthly
    return None


@dataclass(frozen=True, slots=True)
class ComputedLoan:
    """Every figure the response shows, and the reasons some of them are absent."""

    terms: LoanTerms

    # Rates
    effective_reducing_rate: float | None = None
    rate_source: str = ""  # how the reducing rate was arrived at, for the copy

    # Instalment and totals
    computed_emi: float | None = None
    emi_matches_stated: bool | None = None
    emi_difference: float | None = None
    total_repaid: float | None = None

    # Money in and money out
    upfront_fees: float = 0.0
    fee_breakdown: tuple[tuple[str, float], ...] = ()
    bundled_insurance: float = 0.0
    net_disbursed: float | None = None
    true_apr_pct: float | None = None
    total_cost_of_credit: float | None = None

    # Exposure
    penalty: PenaltyExposure | None = None
    prepayment: PrepaymentCost | None = None

    # What could not be computed and why
    uncomputable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_numbers(self) -> bool:
        return self.computed_emi is not None


def _fee_amount(value: float | None, is_pct: bool, principal: float) -> float:
    if value is None:
        return 0.0
    return principal * value / 100.0 if is_pct else value


def compute(terms: LoanTerms, settings: Settings | None = None) -> ComputedLoan:
    """Turn extracted terms into computed figures. Missing inputs → absent outputs."""
    settings = settings or get_settings()

    if not terms.is_computable:
        missing = [
            name
            for name, present in (("principal", terms.principal), ("tenure", terms.tenure_months))
            if not present
        ]
        return ComputedLoan(
            terms=terms,
            uncomputable=tuple(f"no {name} stated in the document" for name in missing),
        )

    principal = float(terms.principal)
    months = int(terms.tenure_months)

    rate, source, exact_emi, uncomputable = _resolve_rate_and_emi(terms, principal, months)

    # Rounded to whole rupees, because that is what a lender actually bills and what
    # the user is shown. It also keeps the printed arithmetic honest: anyone who
    # multiplies the EMI we show by the tenure we show must get the total we show.
    computed_emi = round(exact_emi) if exact_emi is not None else None

    fees, breakdown, insurance = _fees(terms, principal, settings)

    total = total_repayment(computed_emi, months) if computed_emi is not None else None
    net = principal - fees
    apr = true_apr(net, computed_emi, months) if computed_emi is not None and net > 0 else None

    return ComputedLoan(
        terms=terms,
        effective_reducing_rate=rate,
        rate_source=source,
        computed_emi=computed_emi,
        emi_matches_stated=_emi_matches(terms.stated_emi, computed_emi),
        emi_difference=(
            round(computed_emi - terms.stated_emi, 2)
            if computed_emi is not None and terms.stated_emi is not None
            else None
        ),
        total_repaid=total,
        upfront_fees=fees,
        fee_breakdown=breakdown,
        bundled_insurance=insurance,
        net_disbursed=net,
        true_apr_pct=apr,
        total_cost_of_credit=(
            (total + fees + insurance - principal) if total is not None else None
        ),
        penalty=_penalty(terms, computed_emi),
        prepayment=_prepayment(terms, principal, rate, months),
        uncomputable=uncomputable,
    )


def _resolve_rate_and_emi(
    terms: LoanTerms, principal: float, months: int
) -> tuple[float | None, str, float | None, tuple[str, ...]]:
    """The consequential decision: what reducing rate is this loan actually at?"""
    basis = terms.rate_basis
    rate = terms.interest_rate

    if basis == "flat" and rate is not None:
        return (
            flat_to_reducing(principal, rate, months),
            "converted from the flat rate the document quotes",
            emi_flat(principal, rate, months),
            (),
        )

    if basis == "reducing" and rate is not None:
        return (
            rate,
            "as stated in the document",
            emi(principal, rate, months),
            (),
        )

    # Undisclosed, or no basis given at all. Do not assume — derive from the EMI if
    # the document printed one, because the EMI is a fact and the basis is not.
    if terms.stated_emi:
        derived = rate_from_emi(principal, terms.stated_emi, months)
        return (
            derived,
            "worked back from the EMI, because the document never says which basis "
            "its rate is on",
            terms.stated_emi,
            (),
        )

    if rate is not None:
        return (
            None,
            "",
            None,
            (
                "the document gives a rate but never says whether it is flat or "
                "reducing, and prints no EMI — so the real cost cannot be worked out "
                "from it",
            ),
        )

    return (None, "", None, ("no interest rate stated in the document",))


def _fees(
    terms: LoanTerms, principal: float, settings: Settings
) -> tuple[float, tuple[tuple[str, float], ...], float]:
    """One-time charges, GST, and bundled insurance — returned separately.

    Insurance is deliberately **not** part of the disbursal deduction. §5's worked
    example puts the net at ₹97,050 (principal minus fee and GST) and reports the
    bundled ₹1,200 premium as its own red flag, and Phase 1's engine tests pin the
    28.1% APR to that ₹97,050. Folding insurance in here would silently move a
    number the deck already quotes. It is still charged to the borrower — it lands
    in `total_cost_of_credit` and in the hidden-charge list, where it is visible
    rather than buried inside an APR.
    """
    breakdown: list[tuple[str, float]] = []

    processing = _fee_amount(terms.processing_fee, terms.processing_fee_is_pct, principal)
    if processing:
        label = (
            f"Processing fee ({terms.processing_fee:g}% of principal)"
            if terms.processing_fee_is_pct
            else "Processing fee"
        )
        breakdown.append((label, processing))

    for label, value in (
        ("Documentation fee", terms.documentation_fee),
        ("Valuation / legal fee", terms.valuation_legal_fee),
    ):
        if value:
            breakdown.append((label, float(value)))

    if terms.gst_on_fees and breakdown:
        taxable = sum(amount for _, amount in breakdown)
        gst = taxable * settings.gst_rate_pct / 100.0
        breakdown.append((f"GST at {settings.gst_rate_pct:g}% on the above", gst))

    # Stamp duty is a government levy, not a lender fee — no GST on it.
    if terms.stamp_duty:
        breakdown.append(("Stamp duty", float(terms.stamp_duty)))

    return (
        sum(amount for _, amount in breakdown),
        tuple(breakdown),
        float(terms.insurance_premium or 0.0),
    )


def _emi_matches(stated: float | None, computed: float | None) -> bool | None:
    """Within a rupee. Gate 4's bar, and enough to absorb the lender's rounding."""
    if stated is None or computed is None:
        return None
    return abs(stated - computed) <= 1.0


def _penalty(terms: LoanTerms, computed_emi: float | None) -> PenaltyExposure | None:
    """"2% per month on overdue" becomes "one missed EMI costs you ₹607".

    `loan_annual_rate_pct` is deliberately not passed. That opt-in term adds the
    interest the unpaid principal keeps accruing, which is real but is not what the
    document says the penalty is — and §5's ₹607 is the document's own figure. We
    quote what the borrower can verify against their paperwork.
    """
    if computed_emi is None:
        return None
    monthly_pct = penalty_pct_per_month(terms.late_payment_penalty)
    if monthly_pct is None and not terms.bounce_charge:
        return None
    return penalty_exposure(
        instalment=computed_emi,
        penalty_pct_per_month=monthly_pct or 0.0,
        bounce_charge=float(terms.bounce_charge or 0.0),
        months_overdue=1,
    )


def _prepayment(
    terms: LoanTerms, principal: float, rate: float | None, months: int
) -> PrepaymentCost | None:
    """What escaping this loan early would cost, a year in."""
    if rate is None or terms.prepayment_charge is None:
        return None
    after = min(12, max(1, months - 1))
    return prepayment_cost(
        principal=principal,
        annual_rate_pct=rate,
        months=months,
        prepay_after_months=after,
        charge_pct=float(terms.prepayment_charge),
        lock_in_months=int(terms.foreclosure_lock_in or 0),
    )
