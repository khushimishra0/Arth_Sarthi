"""The six-section §5 response, as channel-neutral blocks.

INTEREST · EMI · FEES · PENALTY · HIDDEN CHARGES · ADVICE, in that order, because
that is the order in which a borrower's questions arrive: what does it cost, what do
I pay each month, what comes off the top, what if I slip, what did I miss, what do
I do.

A section is omitted rather than filled with "not stated" — except where the absence
is the point, in which case it appears in HIDDEN CHARGES as a finding. The response
must be readable on a phone by someone who has never taken a formal loan, so no
section assumes the one above it was understood.
"""

from __future__ import annotations

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.features.loan.advisor import Advice
from app.features.loan.computed import ComputedLoan
from app.features.loan.hidden import Finding
from app.utils.money import format_inr

__all__ = ["DISCLAIMER", "format_analysis", "format_unreadable"]

DISCLAIMER = (
    "Educational analysis based only on the document you shared. "
    "Verify all terms with the lender before signing."
)


def format_analysis(
    computed: ComputedLoan,
    findings: list[Finding],
    advice: Advice,
    filename: str = "",
) -> OutboundMessage:
    """The whole §5 layout."""
    blocks: list[Block] = []

    if filename:
        blocks.append(Block.para(f"📄 {filename}"))

    blocks.append(Block.heading(_title(computed)))

    if not computed.has_numbers:
        return _incomplete(computed, findings, advice, blocks)

    blocks.extend(_interest_section(computed))
    blocks.extend(_emi_section(computed))
    blocks.extend(_fees_section(computed))
    blocks.extend(_penalty_section(computed))
    blocks.extend(_hidden_section(findings))
    blocks.extend(_advice_section(computed, advice))

    blocks.append(Block.disclaimer(DISCLAIMER))
    return OutboundMessage.of(*blocks, keyboard=_keyboard())


def _title(computed: ComputedLoan) -> str:
    terms = computed.terms
    detail = []
    if terms.principal and terms.loan_type:
        detail.append(f"{format_inr(terms.principal)} {terms.loan_type} loan")
    elif terms.principal:
        detail.append(f"{format_inr(terms.principal)} loan")
    elif terms.loan_type:
        detail.append(f"{terms.loan_type} loan")
    if terms.tenure_months:
        detail.append(f"{terms.tenure_months} months")
    return f"📋 Loan Analysis — {', '.join(detail)}" if detail else "📋 Loan Analysis"


def _interest_section(computed: ComputedLoan) -> list[Block]:
    terms = computed.terms
    if computed.effective_reducing_rate is None:
        return []

    pairs: list[tuple[str, str]] = []
    if terms.interest_rate is not None:
        basis = terms.rate_basis or "basis not stated"
        pairs.append(("Stated", f"{terms.interest_rate:g}% per year, {basis}"))
    pairs.append(
        ("Actual cost", f"≈{computed.effective_reducing_rate:.1f}% per year, reducing balance")
    )

    blocks = [Block.heading("💰 INTEREST"), Block.key_values(pairs)]

    if terms.rate_basis == "flat":
        blocks.append(
            Block.para(
                f"The lender quotes “flat”, which sounds lower than it is. A flat rate charges "
                f"interest on the whole amount for the whole tenure, even after you have repaid "
                f"most of it. On a reducing balance — how banks quote — this loan really costs "
                f"{computed.effective_reducing_rate:.1f}%. Compare it against that number, not "
                f"{terms.interest_rate:g}%."
            )
        )
    elif computed.rate_source.startswith("worked back"):
        blocks.append(
            Block.para(
                "The document never says whether its rate is flat or reducing, so I worked the "
                "real rate back from the EMI it prints. That figure is what you are actually "
                "paying."
            )
        )
    return blocks


def _emi_section(computed: ComputedLoan) -> list[Block]:
    terms = computed.terms
    pairs: list[tuple[str, str]] = []

    if terms.stated_emi is not None:
        tick = "✓ matches" if computed.emi_matches_stated else "⚠️ does not match"
        pairs.append(
            (
                "Monthly instalment",
                f"Document says {format_inr(terms.stated_emi)} · "
                f"Computed {format_inr(computed.computed_emi)} {tick}",
            )
        )
    else:
        pairs.append(("Monthly instalment", f"{format_inr(computed.computed_emi)} (computed)"))

    if computed.total_repaid is not None:
        pairs.append(
            (f"Total repaid over {terms.tenure_months} months", format_inr(computed.total_repaid))
        )

    return [Block.heading("📅 EMI"), Block.key_values(pairs)]


def _fees_section(computed: ComputedLoan) -> list[Block]:
    if not computed.fee_breakdown:
        return []

    blocks = [
        Block.heading("🧾 FEES"),
        Block.key_values(
            tuple((label, format_inr(amount)) for label, amount in computed.fee_breakdown)
        ),
    ]
    if computed.net_disbursed is not None and computed.upfront_fees:
        blocks.append(
            Block.para(
                f"These come off the top. You will receive "
                f"{format_inr(computed.net_disbursed)} in your account, not "
                f"{format_inr(computed.terms.principal)}."
            )
        )
    return blocks


def _penalty_section(computed: ComputedLoan) -> list[Block]:
    terms = computed.terms
    if computed.penalty is None:
        return []

    pairs: list[tuple[str, str]] = []
    if terms.late_payment_penalty:
        pairs.append(("Late payment", terms.late_payment_penalty))
    if terms.bounce_charge:
        pairs.append(("Bounce charge", format_inr(terms.bounce_charge)))
    pairs.append(("One missed EMI costs you", f"about {format_inr(computed.penalty.total)} extra"))

    return [Block.heading("⚠️ PENALTY"), Block.key_values(pairs)]


def _hidden_section(findings: list[Finding]) -> list[Block]:
    if not findings:
        return [
            Block.heading("🔍 HIDDEN CHARGES"),
            Block.para(
                "I did not find any cost outside the headline rate. That is unusual and good — "
                "but check that every page of the document is here."
            ),
        ]
    return [
        Block.heading("🔍 HIDDEN CHARGES"),
        Block.bullets(tuple(f"🚩 {finding.sentence}" for finding in findings)),
    ]


def _advice_section(computed: ComputedLoan, advice: Advice) -> list[Block]:
    blocks = [Block.heading("💡 ADVICE")]

    if computed.net_disbursed is not None and computed.total_repaid is not None:
        cost = computed.total_cost_of_credit
        blocks.append(
            Block.para(
                f"You borrow {format_inr(computed.net_disbursed)} and repay "
                f"{format_inr(computed.total_repaid)} — {format_inr(cost)} for "
                f"{_tenure_words(computed.terms.tenure_months)} of credit."
            )
        )

    blocks.append(Block.para(f"{advice.headline} {advice.comparison}"))
    blocks.append(Block.para("Before you sign:"))
    blocks.append(Block.steps(advice.negotiation_points))
    blocks.append(Block.para(f"Market rates checked {advice.last_reviewed}."))
    return blocks


def _incomplete(
    computed: ComputedLoan, findings: list[Finding], advice: Advice, blocks: list[Block]
) -> OutboundMessage:
    """Gate 4's third document: report what is missing instead of guessing.

    This path exists because the alternative — filling the gaps with typical values —
    produces a confident, specific, wrong analysis of somebody's real loan.
    """
    blocks.append(
        Block.para(
            "I could not work out what this loan costs, because the document does not say "
            "enough. I am not going to guess — a made-up number here could cost you real money."
        )
    )
    blocks.append(Block.para("What is missing:"))
    blocks.append(Block.bullets(tuple(f"❓ {reason}" for reason in computed.uncomputable)))

    if findings:
        blocks.append(Block.para("What I can tell you:"))
        blocks.append(Block.bullets(tuple(f"🚩 {finding.sentence}" for finding in findings)))

    blocks.append(Block.para("Ask the lender for, in writing:"))
    blocks.append(
        Block.steps(
            (
                "The interest rate, and whether it is flat or reducing.",
                "The exact monthly instalment and the number of instalments.",
                "Every fee that will be deducted before the money reaches you.",
                "What a missed payment costs, and what closing the loan early costs.",
            )
        )
    )
    blocks.append(
        Block.para(
            "Send me the document again once you have those and I will work out the real cost."
        )
    )
    blocks.append(Block.disclaimer(DISCLAIMER))
    return OutboundMessage.of(*blocks, keyboard=_keyboard())


def _tenure_words(months: int | None) -> str:
    if not months:
        return "the tenure"
    if months % 12 == 0:
        years = months // 12
        return "one year" if years == 1 else f"{years} years"
    return f"{months} months"


def _keyboard() -> Keyboard:
    return Keyboard.of(
        [Button(label="📄 Check another loan", action="loan_analysis")],
        [Button(label="🚩 This is a scam message", action="scam_check")],
    )


def format_unreadable(reason: str = "") -> OutboundMessage:
    """Never a naked error — §8."""
    return OutboundMessage.of(
        Block.para(
            "I could not read that document. That is my problem, not yours — some PDFs are "
            "locked or scanned too faintly for me."
        ),
        Block.para("What usually works:"),
        Block.bullets(
            (
                "Send the pages with the loan terms on them, rather than the whole file.",
                "Or photograph the page with the interest rate and fees, in good light.",
                "Or type the numbers out: amount, tenure, rate, EMI, fees.",
            )
        ),
        Block.disclaimer(DISCLAIMER),
        keyboard=Keyboard.of([Button(label="📄 Try again", action="loan_analysis")]),
    )
