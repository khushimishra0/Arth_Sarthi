"""The seven hidden-cost categories of §5 — and silence, which is the eighth.

A hidden charge is *any cost not reflected in the advertised interest rate*. The
headline rate is what the borrower compares; everything that makes the loan cost
more than that rate suggests is, functionally, hidden — whether or not the lender
was trying to hide it.

**Silence is a finding.** This is the part people leave out. If `rate_basis` comes
back `undisclosed`, that is reported as a red flag in its own right, not skipped:
RBI's lending guidelines require the effective rate to be disclosed, so a document
that omits it is telling the borrower something important. The same goes for a
missing prepayment clause, an unstated penalty rate, and a fee schedule that points
at a "schedule of charges" the borrower was never given.

Every rupee figure attached to a finding comes from `computed.py`, which got it from
`finance.py`. Nothing here estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from app.features.loan.computed import ComputedLoan
from app.features.loan.finance import emi, total_repayment
from app.utils.money import format_inr

__all__ = ["Finding", "Severity", "detect"]


class Severity(IntEnum):
    """Ranking only. The user sees a list, not a number."""

    NOTE = 1
    CONCERN = 2
    SERIOUS = 3


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    title: str
    sentence: str
    severity: Severity
    cost_impact: float | None = None

    @property
    def sort_key(self) -> tuple[int, float]:
        return (-int(self.severity), -(self.cost_impact or 0.0))


def detect(computed: ComputedLoan) -> list[Finding]:
    """Every hidden cost and every silence, worst first."""
    findings: list[Finding] = []
    terms = computed.terms

    findings.extend(_flat_rate_finding(computed))
    findings.extend(_disbursal_findings(computed))
    findings.extend(_insurance_finding(computed))
    findings.extend(_gst_findings(computed))
    findings.extend(_prepayment_findings(computed))
    findings.extend(_silence_findings(computed))

    # Category 1 catch-all: an EMI the document states but the terms do not explain.
    if computed.emi_matches_stated is False and computed.emi_difference is not None:
        difference = abs(computed.emi_difference)
        findings.append(
            Finding(
                id="emi_mismatch",
                title="The stated EMI does not match the stated terms",
                sentence=(
                    f"The document says the EMI is {format_inr(terms.stated_emi)}, but its own "
                    f"rate and tenure work out to {format_inr(computed.computed_emi)} — a "
                    f"difference of {format_inr(difference)} a month. Costs that are not in the "
                    f"headline rate are baked into that gap. Ask the lender to explain it in "
                    f"writing before you sign."
                ),
                severity=Severity.SERIOUS,
                cost_impact=difference * (terms.tenure_months or 0),
            )
        )

    findings.sort(key=lambda finding: finding.sort_key)
    return findings


def _flat_rate_finding(computed: ComputedLoan) -> list[Finding]:
    """§5: "the single biggest deception in Indian lending"."""
    terms = computed.terms
    if terms.rate_basis != "flat" or terms.interest_rate is None:
        return []
    if not computed.has_numbers or computed.effective_reducing_rate is None:
        return []

    principal = float(terms.principal)
    months = int(terms.tenure_months)

    # What the same headline number would have cost on a reducing basis — the
    # comparison the borrower is being invited to make, priced.
    honest_emi = round(emi(principal, terms.interest_rate, months))
    extra = total_repayment(computed.computed_emi, months) - total_repayment(honest_emi, months)

    return [
        Finding(
            id="flat_rate_presented_as_comparable",
            title="Flat rate quoted as if it were comparable to a bank's rate",
            sentence=(
                f"The lender quotes {terms.interest_rate:g}% flat. On a reducing balance — the "
                f"way banks quote, and the only way two loans can be compared — this loan "
                f"actually costs {computed.effective_reducing_rate:.1f}%. A flat rate charges "
                f"interest on the full amount for the whole tenure, even after you have paid "
                f"most of it back. Against a genuine {terms.interest_rate:g}% reducing loan you "
                f"pay {format_inr(extra)} more. Compare this loan against "
                f"{computed.effective_reducing_rate:.1f}%, never against {terms.interest_rate:g}%."
            ),
            severity=Severity.SERIOUS,
            cost_impact=extra,
        )
    ]


def _disbursal_findings(computed: ComputedLoan) -> list[Finding]:
    """Fees taken off the top: you sign for one number and receive a smaller one."""
    terms = computed.terms
    if not computed.upfront_fees or computed.net_disbursed is None:
        return []

    lines = [f"{label} — {format_inr(amount)}" for label, amount in computed.fee_breakdown]
    apr_clause = (
        f" Because you pay interest on {format_inr(terms.principal)} but only receive "
        f"{format_inr(computed.net_disbursed)}, the true cost of this loan is "
        f"{computed.true_apr_pct:.1f}% a year, not {computed.effective_reducing_rate:.1f}%."
        if computed.true_apr_pct is not None and computed.effective_reducing_rate is not None
        else ""
    )

    return [
        Finding(
            id="fees_deducted_from_disbursal",
            title="Fees are deducted before you receive the money",
            sentence=(
                f"You will receive {format_inr(computed.net_disbursed)} in your account, not the "
                f"{format_inr(terms.principal)} you are borrowing. Deducted: "
                f"{'; '.join(lines)}.{apr_clause}"
            ),
            severity=Severity.SERIOUS,
            cost_impact=computed.upfront_fees,
        )
    ]


def _insurance_finding(computed: ComputedLoan) -> list[Finding]:
    if not computed.bundled_insurance:
        return []
    return [
        Finding(
            id="bundled_insurance",
            title="Insurance bundled into the loan",
            sentence=(
                f"A {format_inr(computed.bundled_insurance)} insurance premium is attached to "
                f"this loan. Credit-life cover is almost always optional, and bundling it "
                f"without a separate signed opt-in is not permitted. Ask for it to be removed "
                f"unless you specifically want it — that is "
                f"{format_inr(computed.bundled_insurance)} back."
            ),
            severity=Severity.CONCERN,
            cost_impact=computed.bundled_insurance,
        )
    ]


def _gst_findings(computed: ComputedLoan) -> list[Finding]:
    """GST quoted separately inflates a fee; GST unmentioned will still be charged."""
    terms = computed.terms
    if not computed.upfront_fees:
        return []

    if terms.gst_on_fees:
        gst = next(
            (amount for label, amount in computed.fee_breakdown if label.startswith("GST")), 0.0
        )
        if not gst:
            return []
        return [
            Finding(
                id="gst_on_top_of_fees",
                title="GST is charged on top of the fees",
                sentence=(
                    f"The fees carry {format_inr(gst)} of GST on top. The percentage the lender "
                    f"advertises is before tax, so the amount leaving your loan is larger than "
                    f"the fee you were quoted."
                ),
                severity=Severity.NOTE,
                cost_impact=gst,
            )
        ]

    return [
        Finding(
            id="gst_not_mentioned",
            title="The document does not mention GST on its fees",
            sentence=(
                "GST is charged on lending fees in India, but this document does not say so. "
                "Expect roughly 18% more than the quoted fee to be deducted. Ask the lender to "
                "confirm the fee inclusive of tax, in writing."
            ),
            severity=Severity.CONCERN,
        )
    ]


def _prepayment_findings(computed: ComputedLoan) -> list[Finding]:
    """Whether escaping the loan early is possible, and what it costs."""
    terms = computed.terms
    findings: list[Finding] = []

    if terms.foreclosure_lock_in:
        cost = computed.prepayment.charge if computed.prepayment else None
        charge_clause = (
            f" After that, closing it early costs {format_inr(cost)} on top of the outstanding "
            f"balance."
            if cost
            else ""
        )
        findings.append(
            Finding(
                id="foreclosure_lock_in",
                title=f"You cannot close this loan for {terms.foreclosure_lock_in} months",
                sentence=(
                    f"There is a {terms.foreclosure_lock_in}-month lock-in before you are allowed "
                    f"to repay early.{charge_clause} If you expect a lump sum — a bonus, a "
                    f"harvest, a maturity — this clause is what stops you using it to get out."
                ),
                severity=Severity.CONCERN,
                cost_impact=cost,
            )
        )
    elif terms.prepayment_charge and computed.prepayment:
        findings.append(
            Finding(
                id="prepayment_charge",
                title="Repaying early is charged",
                sentence=(
                    f"Closing this loan early costs {terms.prepayment_charge:g}% of whatever is "
                    f"still outstanding — about {format_inr(computed.prepayment.charge)} if you "
                    f"cleared it after a year. Ask whether the charge can be waived; on floating "
                    f"rate loans to individuals, RBI does not allow it at all."
                ),
                severity=Severity.NOTE,
                cost_impact=computed.prepayment.charge,
            )
        )

    return findings


# What a missing field actually means to a borrower. Only fields where the silence
# is itself informative appear here — an absent `lender_name` is a scanning problem,
# not a finding.
_SILENCE: dict[str, tuple[str, str, Severity]] = {
    "prepayment_charge": (
        "The document does not say what repaying early costs",
        "There is no prepayment or foreclosure clause here. That does not mean it is free — "
        "it means the charge is decided elsewhere, usually in a schedule you have not seen. "
        "Ask for it in writing before you sign.",
        Severity.CONCERN,
    ),
    "late_payment_penalty": (
        "The document does not state the late-payment penalty",
        "Nothing here says what happens if you miss an instalment. Penalty rates of 2-3% per "
        "month are common, which compounds fast. Ask for the exact figure in writing.",
        Severity.CONCERN,
    ),
    "processing_fee": (
        "The document does not state a processing fee",
        "No processing fee is mentioned. Most lenders charge one and deduct it from the amount "
        "you receive. Ask what will actually reach your account.",
        Severity.NOTE,
    ),
    "tenure_months": (
        "The document does not state the tenure",
        "Without a tenure, no EMI and no total cost can be worked out — including by you. "
        "Do not sign until the number of instalments is written down.",
        Severity.SERIOUS,
    ),
    "interest_rate": (
        "The document does not state an interest rate",
        "A loan document with no interest rate in it is not something to sign. Ask for the "
        "rate, and for whether it is flat or reducing, in writing.",
        Severity.SERIOUS,
    ),
}


def _silence_findings(computed: ComputedLoan) -> list[Finding]:
    """§5: "Silence is a finding."."""
    terms = computed.terms
    findings: list[Finding] = []

    # The most consequential silence of all.
    if terms.rate_basis == "undisclosed" or (
        terms.rate_basis is None and terms.interest_rate is not None
    ):
        findings.append(
            Finding(
                id="rate_basis_undisclosed",
                title="The document never says whether the rate is flat or reducing",
                sentence=(
                    f"It quotes {terms.interest_rate:g}% but never says on what basis. That is "
                    f"the difference between a loan costing {terms.interest_rate:g}% and the same "
                    f"loan costing nearly double. RBI requires the effective rate to be "
                    f"disclosed; a document that leaves it out is telling you something. Ask for "
                    f"the rate on a reducing balance, in writing, before you sign."
                    if terms.interest_rate is not None
                    else "Ask for the rate on a reducing balance, in writing, before you sign."
                ),
                severity=Severity.SERIOUS,
            )
        )

    if terms.references_external_schedule:
        findings.append(
            Finding(
                id="external_schedule_of_charges",
                title="Fees point at a schedule you were not given",
                sentence=(
                    "The charges refer to a separate 'schedule of charges' that is not part of "
                    "this document. You are being asked to agree to fees you cannot read. Ask "
                    "for that schedule and read it before signing."
                ),
                severity=Severity.CONCERN,
            )
        )

    missing = set(terms.missing_fields)
    for field_name, (title, sentence, severity) in _SILENCE.items():
        if field_name in missing:
            findings.append(
                Finding(
                    id=f"missing_{field_name}",
                    title=title,
                    sentence=sentence,
                    severity=severity,
                )
            )

    return findings
