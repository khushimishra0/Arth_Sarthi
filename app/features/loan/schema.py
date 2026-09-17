"""What the model is allowed to return from a loan document. §5, verbatim.

The same discipline as scam extraction: this transcribes, it does not judge. There
is no `is_expensive` field, no `apr`, no `total_cost` — every number a user sees is
computed by `finance.py` from these raw fields. The model reads "2.5%" off the page;
Python works out what 2.5% costs over 36 months.

**Every field is optional, and that is load-bearing.** A missing field must come
back `null`, never a plausible number. §5: *"Missing fields stay null."* A model that
helpfully invents a processing fee produces an analysis that is confidently wrong
about somebody's actual loan, which is worse than an analysis that says "the
document does not state this". `hidden.py` then treats the silence as a finding in
its own right — RBI requires the effective rate to be disclosed, so a document that
omits it is telling the borrower something important.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["LoanTerms", "RateBasis", "RateType"]

RateBasis = Literal["reducing", "flat", "undisclosed"]
RateType = Literal["fixed", "floating"]


class LoanTerms(BaseModel):
    """The ~18 fields of §5. Anything the document does not state stays `None`."""

    lender_name: str | None = Field(default=None, description="Name of the lender as printed")
    loan_type: str | None = Field(
        default=None,
        description="personal | gold | home | vehicle | business | microfinance | other",
    )
    principal: float | None = Field(default=None, description="Sanctioned amount in rupees")
    tenure_months: int | None = Field(default=None, description="Tenure in months")
    interest_rate: float | None = Field(
        default=None, description="Annual interest rate as printed, e.g. 14 for 14%"
    )
    rate_basis: RateBasis | None = Field(
        default=None,
        description=(
            "reducing | flat | undisclosed. Use 'undisclosed' when the document "
            "gives a rate but never says which basis it is on. Never guess."
        ),
    )
    rate_type: RateType | None = Field(default=None, description="fixed | floating")
    stated_emi: float | None = Field(
        default=None, description="Monthly instalment as printed, if printed"
    )

    processing_fee: float | None = Field(
        default=None, description="Processing fee — a percentage OR a rupee amount"
    )
    processing_fee_is_pct: bool = Field(
        default=False, description="True if processing_fee is a percentage of principal"
    )
    gst_on_fees: bool = Field(default=False, description="True if GST on fees is mentioned")
    insurance_premium: float | None = Field(
        default=None, description="Bundled credit-life or other insurance premium, in rupees"
    )

    prepayment_charge: float | None = Field(
        default=None, description="Foreclosure/prepayment charge as a percentage of outstanding"
    )
    foreclosure_lock_in: int | None = Field(
        default=None, description="Months before prepayment is permitted"
    )
    late_payment_penalty: str | None = Field(
        default=None, description='Verbatim, e.g. "2% per month on overdue amount"'
    )
    bounce_charge: float | None = Field(
        default=None, description="Cheque/mandate bounce charge in rupees"
    )

    documentation_fee: float | None = Field(default=None, description="In rupees")
    valuation_legal_fee: float | None = Field(default=None, description="In rupees")
    stamp_duty: float | None = Field(default=None, description="In rupees")

    # Not in §5's list, but the document either attaches its fee schedule or it does
    # not, and "see our schedule of charges" pointing at a document you were never
    # given is one of the findings §5 explicitly asks for.
    references_external_schedule: bool = Field(
        default=False,
        description="True if fees refer to an external 'schedule of charges' not included",
    )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Everything the document did not state. Input to the silence findings."""
        optional_by_design = {
            "processing_fee_is_pct",
            "gst_on_fees",
            "references_external_schedule",
        }
        return tuple(
            name
            for name in self.__class__.model_fields
            if name not in optional_by_design and getattr(self, name) is None
        )

    @property
    def is_computable(self) -> bool:
        """Whether there is enough here to compute anything at all.

        Without a principal and a tenure there is no EMI, no APR and no total. The
        feature then reports what is missing rather than guessing — §5, and Gate 4's
        deliberately incomplete document.
        """
        return bool(self.principal and self.tenure_months)
