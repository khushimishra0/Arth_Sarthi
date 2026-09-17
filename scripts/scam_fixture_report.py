"""Score the fixture library and report against §12's two passing bars.

    python scripts/scam_fixture_report.py                # uses LLM_PROVIDER from .env
    python scripts/scam_fixture_report.py --rules-only   # free, offline, no model

Two numbers decide Gate 3, and they are not the same number:

  **Band accuracy ≥ 85%** — is the score roughly right?
  **Genuine messages above 50: exactly 0** — this one is a wall, not a target.
    One real bank SMS called a scam teaches a user to ignore us, and the next
    warning is the one that mattered.

The report prints both, plus every miss with the rules that fired, so a failure is
a list of things to fix rather than a percentage to feel bad about.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.features.scam.prompts import EXTRACTION_PROMPT  # noqa: E402
from app.features.scam.schema import ExtractedMessage  # noqa: E402
from app.features.scam.scorer import Band, ScamAssessment  # noqa: E402
from app.features.scam.service import assess  # noqa: E402
from app.llm.preprocess import prepare_image  # noqa: E402

MANIFEST = REPO_ROOT / "tests" / "fixtures" / "scam" / "manifest.yaml"

BAND_ACCURACY_BAR = 0.85
FALSE_POSITIVE_BAR = 50


@dataclass
class Result:
    name: str
    genuine: bool
    expected: Band | None
    assessment: ScamAssessment

    @property
    def band_correct(self) -> bool:
        return self.expected is None or self.assessment.band is self.expected

    @property
    def is_false_positive(self) -> bool:
        return self.genuine and self.assessment.score > FALSE_POSITIVE_BAR


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"No fixture manifest at {path}.\n"
            f"This is Phase 0.4 and it is human work — see "
            f"{path.parent / 'README.md'} for what to collect."
        )
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fixtures = document.get("fixtures") or []
    if not fixtures:
        raise SystemExit(f"{path} has no fixtures in it yet.")
    return fixtures


def extract(entry: dict, provider, settings) -> ExtractedMessage:
    """Text fixtures skip the vision call entirely; image fixtures pay for one."""
    if entry.get("text"):
        return ExtractedMessage(message_text=entry["text"])

    image_path = MANIFEST.parent / entry["file"]
    if not image_path.exists():
        raise SystemExit(f"missing fixture image: {image_path}")

    prepared = prepare_image(image_path.read_bytes(), settings.image_max_edge_px)
    provider.require_vision()
    return provider.read_image(prepared, EXTRACTION_PROMPT, ExtractedMessage)


def run(rules_only: bool) -> int:
    from app.config import get_settings
    from app.llm.factory import get_provider, get_vision_provider

    settings = get_settings()
    fixtures = load_manifest(MANIFEST)

    vision = None if rules_only else get_vision_provider()
    judge = None if rules_only else get_provider()

    if vision is None:
        print("running rules-only: no model consulted, nothing spent\n")
    else:
        print(f"provider: text={judge.name} vision={vision.name} model={vision.model}\n")

    results: list[Result] = []
    for entry in fixtures:
        name = entry.get("file") or (entry.get("text", "")[:48] + "…")
        if vision is None and entry.get("file"):
            print(f"  SKIP  {name} (image fixture needs a vision provider)")
            continue

        extracted = extract(entry, vision, settings)
        assessment = assess(extracted, provider=judge, settings=settings)
        results.append(
            Result(
                name=name,
                genuine=bool(entry.get("genuine", False)),
                expected=Band(entry["expected_band"]) if entry.get("expected_band") else None,
                assessment=assessment,
            )
        )

    return report(results, rules_only=rules_only)


def report(results: list[Result], *, rules_only: bool = False) -> int:
    if not results:
        print("nothing was scored")
        return 1

    print(f"{'':<4}{'score':>6}  {'band':<24} {'expected':<24} fixture")
    print("-" * 100)
    for result in results:
        mark = "ok " if result.band_correct else "MISS"
        if result.is_false_positive:
            mark = "FP!!"
        print(
            f"{mark:<4}{result.assessment.score:>6}  "
            f"{result.assessment.band.value:<24} "
            f"{(result.expected.value if result.expected else '-'):<24} "
            f"{result.name}"
        )

    graded = [r for r in results if r.expected is not None]
    correct = sum(1 for r in graded if r.band_correct)
    accuracy = correct / len(graded) if graded else 0.0
    genuine = [r for r in results if r.genuine]
    false_positives = [r for r in genuine if r.is_false_positive]

    print()
    print("=" * 100)
    print(f"band accuracy            {correct}/{len(graded)}  ({accuracy:.0%})   bar: ≥85%")
    print(
        f"genuine above {FALSE_POSITIVE_BAR}         "
        f"{len(false_positives)}/{len(genuine)}          bar: 0"
    )

    if rules_only:
        print(
            "\nNOTE: rules-only. Scores here are the raw rule total with the model's 40%\n"
            "absent, so they run systematically LOW and band misses on scam fixtures are\n"
            "expected. The false-positive count is still meaningful — a genuine message\n"
            "cannot score lower once the model is added than it does here."
        )

    if false_positives:
        print("\nFALSE POSITIVES — these are shipping blockers:")
        for result in false_positives:
            fired = ", ".join(f"{f.id}({f.weight})" for f in result.assessment.fired)
            print(f"  {result.assessment.score:>3}  {result.name}\n       fired: {fired}")

    misses = [r for r in graded if not r.band_correct and not r.is_false_positive]
    if misses:
        print("\nBAND MISSES:")
        for result in misses:
            fired = ", ".join(f"{f.id}({f.weight})" for f in result.assessment.fired)
            print(
                f"  {result.name}\n"
                f"       got {result.assessment.band.value} "
                f"({result.assessment.score}), expected {result.expected.value}\n"
                f"       fired: {fired or 'nothing'}"
            )

    if rules_only:
        # Only the bar that survives the missing 40% is judged here.
        passed = not false_positives
        print("\nFALSE-POSITIVE BAR:", "PASS" if passed else "FAIL")
        print("(run without --rules-only for the Gate 3 verdict)")
    else:
        passed = accuracy >= BAND_ACCURACY_BAR and not false_positives
        print("\nGATE 3:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="score with the 14 rules alone — no model, no network, no cost",
    )
    return run(parser.parse_args().rules_only)


if __name__ == "__main__":
    raise SystemExit(main())
