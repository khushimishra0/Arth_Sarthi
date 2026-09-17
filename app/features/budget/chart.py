"""The chart. A PNG for Telegram; the web dashboard renders the same data in Phase 7.

Two shapes, because one of them cannot express a deficit. A pie shows how a whole is
divided, so it only works when spending fits inside income. When the month does not
add up, the chart switches to a bar comparison of income against spending — the gap
is the point, and a pie would have to either hide it or lie about it.

**The font is registered explicitly.** matplotlib's default font has no Devanagari
coverage, so Hindi labels come out as empty boxes — and they come out as empty boxes
on the deploy machine even when they looked fine locally, because the local machine
happened to have a system font that covered them. `data/fonts/` is bundled for
exactly this reason. If the font is missing, labels fall back to English rather than
rendering tofu: a chart in the wrong language is readable, a chart of blank squares
is not.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # No display on a server. Must be set before pyplot is imported.

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from app.features.budget.schema import Budget, load_benchmarks  # noqa: E402
from app.utils.money import format_inr  # noqa: E402

__all__ = ["CATEGORY_COLOURS", "devanagari_available", "render_chart"]

log = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parents[3] / "data" / "fonts"

# Fixed, so the same category is the same colour in every chart a user ever sees —
# and the same colour on the web dashboard. Savings is the only green.
CATEGORY_COLOURS: dict[str, str] = {
    "rent": "#4C6EF5",
    "food": "#F59F00",
    "travel": "#7950F2",
    "phone": "#22B8CF",
    "medical": "#FA5252",
    "education": "#4DABF7",
    "emi": "#E8590C",
    "other": "#868E96",
    "savings": "#2F9E44",
}

_DEFICIT_COLOUR = "#C92A2A"
_INCOME_COLOUR = "#1971C2"


def _register_fonts() -> str | None:
    """Add every bundled font to matplotlib and return a Devanagari family name."""
    if not FONTS_DIR.is_dir():
        return None
    family: str | None = None
    for path in sorted(FONTS_DIR.glob("*.tt[fc]")):
        try:
            font_manager.fontManager.addfont(str(path))
            name = font_manager.FontProperties(fname=str(path)).get_name()
            if "devanagari" in name.lower():
                family = name
        except Exception:  # pragma: no cover - a corrupt font must not break charts
            log.warning("could not register bundled font %s", path.name)
    return family


_DEVANAGARI_FAMILY = _register_fonts()

# Noto Sans Devanagari ships as a variable font with no static bold face, so every
# bold title makes matplotlib log "Failed to find font weight bold". It falls back
# to regular weight and the chart is fine; the message is noise that would otherwise
# appear on every Hindi chart we ever render.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


def devanagari_available() -> bool:
    """Whether Hindi labels can be drawn without producing empty boxes."""
    return _DEVANAGARI_FAMILY is not None


def _font_stack(language: str) -> list[str]:
    if language == "hi" and _DEVANAGARI_FAMILY:
        return [_DEVANAGARI_FAMILY, "DejaVu Sans"]
    return ["DejaVu Sans"]


def _slices(budget: Budget) -> list[tuple[str, str, float, str]]:
    """`(key, label, amount, colour)` in the benchmark file's order, savings last."""
    benchmarks = load_benchmarks()
    out: list[tuple[str, str, float, str]] = []
    for key, spec in benchmarks.categories.items():
        amount = budget.categories.get(key, 0.0)
        if amount > 0:
            out.append((key, spec["label"], amount, CATEGORY_COLOURS.get(key, "#868E96")))
    if budget.surplus > 0:
        out.append(
            ("savings", benchmarks.savings["label"], budget.surplus, CATEGORY_COLOURS["savings"])
        )
    return out


def render_chart(budget: Budget, language: str = "en") -> bytes:
    """A labelled PNG of this month. Pie when it adds up, bars when it does not."""
    plt.rcParams["font.family"] = _font_stack(language)
    figure = None
    try:
        figure = _bar_figure(budget) if budget.in_deficit else _pie_figure(budget)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=140, bbox_inches="tight", facecolor="white")
        return buffer.getvalue()
    finally:
        if figure is not None:
            plt.close(figure)


def _pie_figure(budget: Budget):
    slices = _slices(budget)
    figure, axes = plt.subplots(figsize=(6.4, 5.2))

    amounts = [amount for _, _, amount, _ in slices]
    colours = [colour for _, _, _, colour in slices]
    labels = [
        f"{label}\n{format_inr(amount)} · {amount / budget.income * 100:.0f}%"
        for _, label, amount, _ in slices
    ]
    # Savings pulled slightly out of the wheel — the feature exists to grow it, so
    # it should be the slice the eye lands on.
    explode = [0.06 if key == "savings" else 0.0 for key, _, _, _ in slices]

    axes.pie(
        amounts,
        labels=labels,
        colors=colours,
        explode=explode,
        startangle=90,
        counterclock=False,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 9},
    )
    axes.set_title(
        f"Your month — {format_inr(budget.income)}", fontsize=13, fontweight="bold", pad=16
    )
    axes.axis("equal")
    return figure


def _bar_figure(budget: Budget):
    """A deficit, stated plainly. §7: a pie cannot show one."""
    figure, axes = plt.subplots(figsize=(6.4, 4.4))

    bars = axes.bar(
        ["Money in", "Money out"],
        [budget.income, budget.total_spent],
        color=[_INCOME_COLOUR, _DEFICIT_COLOUR],
        width=0.55,
    )
    for bar, value in zip(bars, (budget.income, budget.total_spent), strict=True):
        axes.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            format_inr(value),
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )

    shortfall = abs(budget.surplus)
    axes.set_title(
        f"You are short {format_inr(shortfall)} this month",
        fontsize=13,
        fontweight="bold",
        color=_DEFICIT_COLOUR,
        pad=16,
    )
    axes.set_ylim(0, max(budget.income, budget.total_spent) * 1.18)
    axes.spines[["top", "right"]].set_visible(False)
    axes.tick_params(axis="y", labelsize=9)
    axes.grid(axis="y", alpha=0.25)
    axes.set_axisbelow(True)
    return figure
