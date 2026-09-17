"""Score the loan fixture set against Gate 4's three bars.

    python scripts/loan_fixture_report.py

  **EMI within ₹1** where the document is internally consistent.
  **The flat-rate document is caught.**
  **The incomplete document reports what is missing** rather than guessing.

Unlike the scam report there is no useful offline mode: reading a real PDF is the
whole test, and that needs a key. What *is* checked without one is the routing —
whether each document goes down the free path or the expensive one — because that
runs on pdfplumber alone.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.features.loan.computed import ComputedLoan  # noqa: E402
from app.features.loan.extractor import ExtractionRoute, choose_route, extract_terms  # noqa: E402
from app.features.loan.service import analyse_terms  # noqa: E402
from app.llm.preprocess import pdf_text_yield  # noqa: E402

MANIFEST = REPO_ROOT / "tests" / "fixtures" / "loan" / "manifest.yaml"

EMI_TOLERANCE_RUPEES = 1.0


@dataclass
class Result:
    name: str
    expect: dict
    route: ExtractionRoute
    computed: ComputedLoan | None = None
    finding_ids: tuple[str, ...] = ()
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"No fixture manifest at {path}.\n"
            f"This is Phase 0.4 and it is human work — see {path.parent / 'README.md'}."
        )
    fixtures = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("fixtures") or []
    if not fixtures:
        raise SystemExit(f"{path} has no fixtures in it yet.")
    return fixtures


def check(result: Result) -> None:
    """Compare what the product said against what a human read off the document."""
    expect = result.expect
    computed = result.computed

    if "route" in expect and result.route.value != expect["route"]:
        result.failures.append(f"routed as {result.route.value}, expected {expect['route']}")

    if expect.get("incomputable"):
        if computed is not None and computed.has_numbers:
            result.failures.append(
                f"produced an EMI of {computed.computed_emi} for a document that does not "
                f"state enough — it must refuse to guess"
            )
    elif computed is None or not computed.has_numbers:
        result.failures.append("could not compute anything")
        return

    if computed is None:
        return

    terms = computed.terms
    for key, actual in (
        ("principal", terms.principal),
        ("tenure_months", terms.tenure_months),
        ("rate_basis", terms.rate_basis),
        ("interest_rate", terms.interest_rate),
    ):
        if key in expect and actual != expect[key]:
            result.failures.append(f"{key}: read {actual!r}, expected {expect[key]!r}")

    if "emi" in expect and computed.computed_emi is not None:
        gap = abs(computed.computed_emi - expect["emi"])
        if gap > EMI_TOLERANCE_RUPEES:
            result.failures.append(
                f"EMI off by ₹{gap:.2f} (computed {computed.computed_emi}, "
                f"document {expect['emi']})"
            )

    missed = set(expect.get("catches", [])) - set(result.finding_ids)
    if missed:
        result.failures.append(f"did not catch: {', '.join(sorted(missed))}")


def run(routing_only: bool) -> int:
    from app.config import get_settings
    from app.llm.factory import get_vision_provider

    settings = get_settings()
    fixtures = load_manifest(MANIFEST)
    provider = None if routing_only else get_vision_provider()

    if provider is None:
        print("routing only: pdfplumber decides the route, nothing is read or spent\n")
    else:
        print(f"provider: {provider.name} model={provider.model}\n")

    results: list[Result] = []
    for entry in fixtures:
        path = MANIFEST.parent / entry["file"]
        if not path.exists():
            raise SystemExit(f"missing fixture: {path}")

        pdf = path.read_bytes()
        _, _, per_page = pdf_text_yield(pdf)
        result = Result(
            name=entry["file"],
            expect=entry.get("expect", {}),
            route=choose_route(per_page, settings),
        )

        if provider is not None:
            extraction = extract_terms(pdf, provider, settings)
            result.route = extraction.route
            computed, findings, _ = analyse_terms(extraction.terms, settings)
            result.computed = computed
            result.finding_ids = tuple(f.id for f in findings)

        check(result)
        results.append(result)

    return report(results, routing_only=routing_only)


def report(results: list[Result], *, routing_only: bool) -> int:
    print(f"{'':<5}{'route':<12} fixture")
    print("-" * 92)
    for result in results:
        print(f"{'ok' if result.ok else 'FAIL':<5}{result.route.value:<12} {result.name}")
        for failure in result.failures:
            print(f"      ↳ {failure}")

    print()
    print("=" * 92)

    if routing_only:
        passed = all(r.ok for r in results)
        print("ROUTING:", "PASS" if passed else "FAIL")
        print("(run without --routing-only for the Gate 4 verdict — needs GEMINI_API_KEY)")
        return 0 if passed else 1

    graded = [r for r in results if r.computed is not None]
    emi_checked = [r for r in graded if "emi" in r.expect]
    emi_ok = [r for r in emi_checked if not any("EMI off" in f for f in r.failures)]

    flat = [r for r in graded if r.expect.get("rate_basis") == "flat"]
    flat_caught = [r for r in flat if "flat_rate_presented_as_comparable" in r.finding_ids]

    incomplete = [r for r in results if r.expect.get("incomputable")]
    incomplete_ok = [r for r in incomplete if r.ok]

    print(f"EMI within ₹1              {len(emi_ok)}/{len(emi_checked)}     bar: all")
    print(f"flat-rate documents caught {len(flat_caught)}/{len(flat)}     bar: all")
    print(f"incomplete handled         {len(incomplete_ok)}/{len(incomplete)}     bar: all")

    if not flat:
        print("\n⚠ no flat-rate document in the set — Gate 4's second bar is untested")
    if not incomplete:
        print("⚠ no incomplete document in the set — Gate 4's third bar is untested")

    passed = (
        len(emi_ok) == len(emi_checked)
        and len(flat_caught) == len(flat)
        and len(incomplete_ok) == len(incomplete)
        and bool(flat)
        and bool(incomplete)
    )
    print("\nGATE 4:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routing-only",
        action="store_true",
        help="check text-vs-scanned routing with pdfplumber alone — no key, no cost",
    )
    return run(parser.parse_args().routing_only)


if __name__ == "__main__":
    raise SystemExit(main())
