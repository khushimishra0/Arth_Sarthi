"""Gate 3's measurement, wired into the suite so it turns green the day fixtures land.

Skips loudly while `tests/fixtures/scam/manifest.yaml` is absent — that file is
Phase 0.4, it is human work, and no amount of code makes it appear. What this does
guarantee is that the harness itself is not the thing that breaks on the day the
screenshots arrive: text fixtures in the manifest are scored right now, for free.
"""

from __future__ import annotations

import pytest

from app.features.scam.rules import evaluate
from app.features.scam.schema import ExtractedMessage
from app.features.scam.scorer import Band, score
from scripts.scam_fixture_report import (
    BAND_ACCURACY_BAR,
    FALSE_POSITIVE_BAR,
    MANIFEST,
    Result,
    load_manifest,
)

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(),
    reason=(
        "no scam fixtures yet — Phase 0.4. "
        "See tests/fixtures/scam/README.md for what to collect."
    ),
)


def _text_results() -> list[Result]:
    """Score only the typed fixtures. Image fixtures need a key and cost money, so
    they belong in `scripts/scam_fixture_report.py`, run deliberately."""
    results = []
    for entry in load_manifest(MANIFEST):
        if not entry.get("text"):
            continue
        assessment = score(evaluate(ExtractedMessage(message_text=entry["text"])))
        results.append(
            Result(
                name=entry["text"][:48],
                genuine=bool(entry.get("genuine", False)),
                expected=Band(entry["expected_band"]) if entry.get("expected_band") else None,
                assessment=assessment,
            )
        )
    return results


def test_no_genuine_fixture_scores_above_fifty():
    """§13. A wall, not a target — one failure here blocks the demo."""
    offenders = [
        f"{r.name} scored {r.assessment.score} ({[x.id for x in r.assessment.fired]})"
        for r in _text_results()
        if r.is_false_positive
    ]
    assert offenders == [], f"genuine messages above {FALSE_POSITIVE_BAR}: {offenders}"


def test_no_scam_fixture_is_waved_through():
    """The rules alone must at least raise an eyebrow at every known scam.

    Band *accuracy* is not asserted here on purpose. This runs rules-only, without
    the model's 40%, so every score is systematically low and a scam that belongs in
    "high risk" legitimately lands in "be careful". The full-pipeline verdict comes
    from `scripts/scam_fixture_report.py`, run deliberately against a real key.
    """
    waved_through = [
        f"{r.name} scored {r.assessment.score} with no flags"
        for r in _text_results()
        if not r.genuine and not r.assessment.fired
    ]
    assert waved_through == [], f"known scams that fired nothing: {waved_through}"


def test_the_bar_constants_match_the_plan():
    """§12: ≥85% band accuracy, and zero genuine messages above 50."""
    assert BAND_ACCURACY_BAR == 0.85
    assert FALSE_POSITIVE_BAR == 50
