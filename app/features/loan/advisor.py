"""The verdict and the negotiation points. Explains and warns; never recommends.

Two jobs.

**Placing the loan.** "28% APR" means nothing to someone who has never been quoted
a rate. "Expensive, but below the 40-60% a moneylender charges and above the 11-16%
a bank would" means something immediately. That is the entire value of
`data/benchmarks.yaml`.

**Making the offer negotiable.** Most borrowers do not know a processing fee is
negotiable, that bundled insurance can be declined, or that they are entitled to
ask for the rate on a reducing balance in writing. Each point below is tied to a
rupee figure computed from their own document, because "ask them to waive the fee"
is advice and "ask them to waive the fee, it is worth ₹2,950" is leverage.

Standing constraint 4 holds throughout: no named lender, no named product. Only
categories — "a gold loan", "the bank where your salary is credited" — and only as
somewhere cheaper to ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

from app.features.loan.computed import ComputedLoan
from app.features.loan.hidden import Finding
from app.utils.money import format_inr, in_words

__all__ = ["Advice", "Benchmarks", "Verdict", "advise", "load_benchmarks"]

BENCHMARKS_PATH = Path(__file__).resolve().parents[3] / "data" / "benchmarks.yaml"


class Verdict(StrEnum):
    CHEAPER_THAN_BANKS = "cheaper_than_banks"
    BANK_RATE = "bank_rate"
    EXPENSIVE = "expensive"
    VERY_EXPENSIVE = "very_expensive"
    PREDATORY = "predatory"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Benchmarks:
    last_reviewed: str
    loan_types: dict
    informal: dict
    alternatives: dict

    def bands_for(self, loan_type: str | None) -> dict:
        key = (loan_type or "other").lower()
        return self.loan_types.get(key, self.loan_types["other"])

    def alternatives_for(self, loan_type: str | None) -> list[str]:
        key = (loan_type or "other").lower()
        return self.alternatives.get(key, self.alternatives["other"])


@lru_cache
def load_benchmarks(path: Path | None = None) -> Benchmarks:
    document = yaml.safe_load((path or BENCHMARKS_PATH).read_text(encoding="utf-8"))
    return Benchmarks(
        last_reviewed=str(document["last_reviewed"]),
        loan_types=document["loan_types"],
        informal=document["informal"],
        alternatives=document["alternatives"],
    )


@dataclass(frozen=True, slots=True)
class Advice:
    verdict: Verdict
    headline: str
    comparison: str
    negotiation_points: tuple[str, ...]
    last_reviewed: str


def classify(apr: float | None, bands: dict, informal: dict) -> Verdict:
    if apr is None:
        return Verdict.UNKNOWN
    bank_low, bank_high = bands["bank"]
    _, nbfc_high = bands["nbfc"]
    informal_low = informal["range"][0]

    if apr < bank_low:
        return Verdict.CHEAPER_THAN_BANKS
    if apr <= bank_high:
        return Verdict.BANK_RATE
    if apr <= nbfc_high:
        return Verdict.EXPENSIVE
    if apr < informal_low:
        return Verdict.VERY_EXPENSIVE
    return Verdict.PREDATORY


_HEADLINES: dict[Verdict, str] = {
    Verdict.CHEAPER_THAN_BANKS: "This is a good rate — cheaper than most banks would offer you.",
    Verdict.BANK_RATE: "This is priced about where a bank would price it. Reasonable.",
    Verdict.EXPENSIVE: "This is expensive, but it is not predatory.",
    Verdict.VERY_EXPENSIVE: "This is very expensive. Borrow less, or borrow elsewhere if you can.",
    Verdict.PREDATORY: (
        "This is priced like a moneylender, not a lender. Please do not sign it today."
    ),
    Verdict.UNKNOWN: (
        "I cannot tell you what this loan really costs, because the document does not "
        "say enough."
    ),
}


def advise(computed: ComputedLoan, findings: list[Finding]) -> Advice:
    """Place the loan against the market, then say what to ask for."""
    benchmarks = load_benchmarks()
    terms = computed.terms
    bands = benchmarks.bands_for(terms.loan_type)
    apr = computed.true_apr_pct

    verdict = classify(apr, bands, benchmarks.informal)
    bank_low, bank_high = bands["bank"]
    informal_low, informal_high = benchmarks.informal["range"]

    if verdict is Verdict.UNKNOWN:
        comparison = (
            "A comparable "
            f"{bands['label']} from a bank costs {bank_low:g}-{bank_high:g}% a year. "
            "Get the missing terms in writing and you can compare it yourself."
        )
    else:
        comparison = (
            f"At {apr:.1f}% a year all-in, this sits against a bank's {bank_low:g}-{bank_high:g}% "
            f"for a {bands['label']}, and a {benchmarks.informal['label']}'s "
            f"{informal_low:g}-{informal_high:g}%."
        )

    return Advice(
        verdict=verdict,
        headline=_HEADLINES[verdict],
        comparison=comparison,
        negotiation_points=_negotiation_points(computed, findings, benchmarks),
        last_reviewed=benchmarks.last_reviewed,
    )


def _negotiation_points(
    computed: ComputedLoan, findings: list[Finding], benchmarks: Benchmarks
) -> tuple[str, ...]:
    """Each point tied to a rupee figure from this borrower's own document."""
    terms = computed.terms
    fired = {finding.id for finding in findings}
    points: list[str] = []

    if "flat_rate_presented_as_comparable" in fired or "rate_basis_undisclosed" in fired:
        # No markup here — a feature returns text, and the channel decides how to
        # emphasise it. Asterisks would arrive as literal asterisks on Telegram.
        points.append(
            "Ask for the rate on a reducing balance, in writing, and ask them to show you the "
            "repayment schedule month by month. A lender who will not put that in writing is "
            "telling you something."
        )

    if computed.bundled_insurance:
        points.append(
            f"Ask them to remove the bundled insurance. It is usually optional and worth "
            f"{format_inr(computed.bundled_insurance)} to you."
        )

    if computed.upfront_fees:
        points.append(
            f"Ask them to waive or halve the processing fee. This is routinely negotiable and "
            f"worth {format_inr(computed.upfront_fees)}."
        )

    if terms.foreclosure_lock_in:
        points.append(
            f"Do not accept the {terms.foreclosure_lock_in}-month lock-in if you expect any "
            f"lump sum — a bonus, a harvest, a policy maturity — inside that window."
        )

    for alternative in benchmarks.alternatives_for(terms.loan_type)[:2]:
        points.append(f"Before signing, ask about {alternative}.")

    if computed.total_cost_of_credit and terms.principal:
        per_10k = computed.total_cost_of_credit * 10_000 / float(terms.principal)
        points.append(
            f"Borrow less if you can. Every {in_words(10_000)} you do not borrow saves you "
            f"roughly {format_inr(per_10k)} over this tenure."
        )

    points.append(
        "Take the document home and read it once more tomorrow. Nothing genuine expires overnight."
    )
    return tuple(points)
