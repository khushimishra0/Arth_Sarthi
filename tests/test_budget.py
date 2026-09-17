"""Budget coach: parsing, thresholds, goals, charts and the §7 worked example.

Two things here are product requirements rather than implementation details, and both
are asserted deliberately.

The **thresholds are not 50/30/20**. §7 is explicit that the usual rule is written
for high incomes and is "quietly insulting at ₹15,000 a month". If someone
"simplifies" the benchmark file back to 50/30/20, these tests fail.

**Food below 15% is under-spending, not thrift.** It is the one category where
spending too little is the finding, because at this income level it usually means
somebody is eating less than they should.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from PIL import Image

from app.channels.base import render_plain
from app.features.budget.analyser import MAX_ADVICE_POINTS, analyse
from app.features.budget.chart import CATEGORY_COLOURS, devanagari_available, render_chart
from app.features.budget.formatter import format_needs_more
from app.features.budget.goals import GoalKind, build_plan, saving_if_reduced
from app.features.budget.parser import looks_like_budget_input, parse, parse_amount
from app.features.budget.schema import Budget, CategoryBand, load_benchmarks
from app.features.budget.service import analyse_budget, current_month, run_budget

TODAY = date(2026, 8, 2)

# §7's worked example: income ₹25,000 · rent ₹8,000 · food ₹4,000 · travel ₹3,000
WORKED = Budget(income=25_000, categories={"rent": 8_000, "food": 4_000, "travel": 3_000})


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8000", 8_000),
        ("8,000", 8_000),
        ("₹8000", 8_000),
        ("₹ 8,000", 8_000),
        ("8k", 8_000),
        ("8K", 8_000),
        ("8 hazaar", 8_000),
        ("8 हज़ार", 8_000),
        ("1.5 lakh", 150_000),
        ("25 लाख", 2_500_000),
    ],
)
def test_amounts_are_read_the_way_people_type_them(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    "text", ["skip", "none", "nahi", "नहीं", "0", "", "hello", "8000 rupees ok"]
)
def test_non_amounts_return_none(text):
    assert parse_amount(text) is None


# ---------------------------------------------------------------------------
# Free-text parsing
# ---------------------------------------------------------------------------


def test_the_worked_example_parses_exactly():
    result = parse("income 25000 rent 8000 food 4000 travel 3000")
    assert result.budget.income == 25_000
    assert result.budget.categories == {"rent": 8_000, "food": 4_000, "travel": 3_000}
    assert result.is_confident


def test_order_does_not_matter():
    a = parse("income 25000 rent 8000 food 4000")
    b = parse("food 4000 rent 8000 income 25000")
    assert a.budget == b.budget


def test_hinglish_labels_are_understood():
    """Slide 9's segments do not type in English."""
    result = parse("kamai 25k kiraya 8k khana 4000 dawai 500 emi 3000")
    assert result.budget.income == 25_000
    assert result.budget.categories == {
        "rent": 8_000,
        "food": 4_000,
        "medical": 500,
        "emi": 3_000,
    }
    assert result.is_confident


def test_devanagari_labels_are_understood():
    result = parse("आय 20000 किराया 7000 खाना 5000")
    assert result.budget.income == 20_000
    assert result.budget.categories["rent"] == 7_000
    assert result.budget.categories["food"] == 5_000


@pytest.mark.parametrize("separator", [" ", ": ", " = ", " - ", " is "])
def test_common_separators_are_tolerated(separator):
    result = parse(f"income{separator}25000 rent{separator}8000")
    assert result.budget.income == 25_000
    assert result.budget.categories["rent"] == 8_000


def test_an_unknown_label_lands_in_other():
    result = parse("income 20000 misc 2000")
    assert result.budget.categories["other"] == 2_000


# ---------------------------------------------------------------------------
# Ambiguity — §7's fallback to the guided flow
# ---------------------------------------------------------------------------


def test_unlabelled_numbers_are_not_guessed_at():
    """Acting on these would draw a chart of a month the user does not have."""
    result = parse("income 25000 8000 4000")
    assert not result.is_confident
    assert result.unrecognised_numbers == ("8000", "4000")
    assert "could not tell what these numbers were for" in result.ambiguity


def test_two_different_figures_for_one_category_is_ambiguous():
    result = parse("income 25000 rent 8000 rent 9000")
    assert not result.is_confident
    assert result.duplicate_labels == ("rent",)
    assert "two different figures" in result.ambiguity


def test_the_same_figure_twice_is_a_repetition_not_a_conflict():
    result = parse("income 25000 rent 8000 rent 8000")
    assert result.is_confident
    assert result.budget.categories["rent"] == 8_000


def test_a_missing_income_is_ambiguous():
    result = parse("rent 8000 food 4000")
    assert not result.is_confident
    assert "monthly income" in result.ambiguity


def test_small_numbers_are_not_mistaken_for_expenses():
    """"2 kids" and "3 months" must not each become a mystery line item."""
    result = parse("income 25000 rent 8000 i have 2 kids and 3 rooms")
    assert result.is_confident
    assert result.unrecognised_numbers == ()


def test_prose_is_not_a_budget():
    result = parse("my loan was rejected yesterday")
    assert result.budget.income == 0
    assert result.budget.categories == {}
    assert not looks_like_budget_input("my loan was rejected yesterday")


def test_looks_like_budget_input_is_stricter_than_the_intent_classifier():
    """The classifier decides whether to offer the coach; this decides whether to run it."""
    assert looks_like_budget_input("income 25000 rent 8000")
    assert looks_like_budget_input("rent 8000 food 4000")  # two categories, no income
    assert not looks_like_budget_input("emi 4801")  # one line, could be anything


# ---------------------------------------------------------------------------
# Thresholds — §7, not 50/30/20
# ---------------------------------------------------------------------------


def test_the_benchmarks_are_not_fifty_thirty_twenty():
    """§7: that rule is "quietly insulting at ₹15,000 a month"."""
    benchmarks = load_benchmarks()
    assert benchmarks.categories["rent"]["healthy_max"] == 30
    assert benchmarks.categories["rent"]["watch_max"] == 40
    assert benchmarks.categories["emi"]["watch_max"] == 35  # the debt-trap threshold
    assert benchmarks.savings["healthy_min"] == 20


@pytest.mark.parametrize(
    ("category", "share", "band"),
    [
        ("rent", 25, CategoryBand.HEALTHY),
        ("rent", 32, CategoryBand.WATCH),
        ("rent", 45, CategoryBand.DANGER),
        ("food", 25, CategoryBand.HEALTHY),
        ("food", 35, CategoryBand.WATCH),
        ("food", 50, CategoryBand.DANGER),
        ("travel", 8, CategoryBand.HEALTHY),
        ("travel", 12, CategoryBand.WATCH),
        ("travel", 20, CategoryBand.DANGER),
        ("emi", 15, CategoryBand.HEALTHY),
        ("emi", 30, CategoryBand.WATCH),
        ("emi", 40, CategoryBand.DANGER),
    ],
)
def test_shares_land_in_the_right_band(category, share, band):
    assert load_benchmarks().band_for(category, share) is band


def test_food_below_fifteen_percent_is_under_spending_not_thrift():
    """§7: at this income it usually means somebody is eating less than they should."""
    assert load_benchmarks().band_for("food", 12) is CategoryBand.UNDER


def test_no_other_category_treats_low_spending_as_a_problem():
    """Spending little on travel or phone is good news. Only food is inverted."""
    benchmarks = load_benchmarks()
    with_under = [k for k, spec in benchmarks.categories.items() if "under_min" in spec]
    assert with_under == ["food"]


def test_savings_is_a_floor_not_a_ceiling():
    benchmarks = load_benchmarks()
    assert benchmarks.savings_band(40) is CategoryBand.HEALTHY
    assert benchmarks.savings_band(15) is CategoryBand.WATCH
    assert benchmarks.savings_band(5) is CategoryBand.DANGER


# ---------------------------------------------------------------------------
# The worked example
# ---------------------------------------------------------------------------


def test_the_worked_examples_shares_match_the_plan():
    """§7: Rent 32% · Food 16% · Travel 12% · Savings 40%."""
    assert WORKED.share_of_income("rent") == pytest.approx(32)
    assert WORKED.share_of_income("food") == pytest.approx(16)
    assert WORKED.share_of_income("travel") == pytest.approx(12)
    assert WORKED.surplus_pct == pytest.approx(40)


def test_the_worked_examples_verdicts_match_the_plan():
    analysis = analyse(WORKED)
    bands = {v.key: v.band for v in analysis.verdicts}
    assert bands["rent"] is CategoryBand.WATCH  # "slightly high"
    assert bands["food"] is CategoryBand.HEALTHY
    assert bands["travel"] is CategoryBand.WATCH
    assert analysis.savings_band is CategoryBand.HEALTHY


def test_the_headline_credits_a_rare_savings_rate():
    assert "genuinely rare" in analyse(WORKED).headline


def test_advice_is_capped_at_three_points():
    """A list of eight things to fix is a list nobody starts."""
    busy = Budget(
        income=15_000,
        categories={"rent": 7_000, "food": 1_500, "travel": 3_000, "emi": 6_000},
    )
    assert len(analyse(busy).advice) <= MAX_ADVICE_POINTS


def test_a_healthy_month_is_told_to_move_the_money_out():
    """§7's first advice point: money sitting in a savings account gets spent."""
    assert any("payday" in point for point in analyse(WORKED).advice)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


def test_the_emergency_fund_matches_the_plans_figure():
    """§7: ₹45,000 — 3 months of ₹15,000 of expenses — reached in 4.5 months."""
    goal = build_plan(WORKED, TODAY).goal
    assert goal.kind is GoalKind.EMERGENCY_FUND
    assert goal.target == pytest.approx(45_000)
    assert goal.months == pytest.approx(4.5)
    assert goal.months_label == "4.5 months"


def test_every_goal_carries_a_real_date():
    """§7: "Save more" is advice nobody acts on."""
    goal = build_plan(WORKED, TODAY).goal
    assert goal.target_date > TODAY
    assert goal.date_label == goal.target_date.strftime("%d %B %Y")


def test_the_emergency_fund_is_three_times_expenses_not_income():
    goal = build_plan(WORKED, TODAY).goal
    assert goal.target == pytest.approx(WORKED.total_spent * 3)
    assert goal.target != pytest.approx(WORKED.income * 3)


def test_a_small_surplus_starts_with_the_reachable_buffer():
    """₹5,000 is winnable. Leading with a 3-month fund teaches people saving is
    for other people."""
    small = Budget(income=18_000, categories={"rent": 6_000, "food": 5_000, "emi": 5_000})
    plan = build_plan(small, TODAY)
    assert plan.goal.kind is GoalKind.BUFFER
    assert plan.goal.target == 5_000


def test_the_buffer_is_skipped_when_one_month_already_clears_it():
    """Naming a goal the user reaches this month is patronising, not motivating."""
    plan = build_plan(WORKED, TODAY)
    assert plan.goal.kind is GoalKind.EMERGENCY_FUND
    assert all(g.kind is not GoalKind.BUFFER for g in plan.later)


def test_a_near_zero_surplus_gets_the_daily_challenge():
    """Slide 13's entry rung."""
    tight = Budget(income=12_000, categories={"rent": 5_000, "food": 4_000, "emi": 2_900})
    plan = build_plan(tight, TODAY)
    assert plan.goal is None
    assert plan.challenge.kind is GoalKind.DAILY_CHALLENGE


def test_the_daily_challenge_is_framed_as_a_year_not_a_wait():
    """₹5,000 at ₹10/day is 16 months away — leading with that argues against the habit."""
    tight = Budget(income=12_000, categories={"rent": 5_000, "food": 4_000, "emi": 2_900})
    challenge = build_plan(tight, TODAY).challenge
    assert challenge.target == pytest.approx(3_650)
    assert challenge.months == 12
    assert challenge.target_date.year == TODAY.year + 1


def test_a_deficit_gets_no_savings_goal():
    """Telling someone ₹2,500 short each month to build a fund is not advice."""
    deficit = Budget(income=15_000, categories={"rent": 7_000, "food": 5_000, "emi": 5_500})
    plan = build_plan(deficit, TODAY)
    assert plan.goal is None
    assert plan.challenge is not None  # still offered something


def test_a_users_own_goal_joins_the_ladder():
    with_goal = Budget(
        income=25_000,
        categories={"rent": 8_000, "food": 4_000, "travel": 3_000},
        goal_name="School fees",
        goal_amount=30_000,
    )
    plan = build_plan(with_goal, TODAY)
    kinds = [g.kind for g in ([plan.goal] + list(plan.later))]
    assert GoalKind.OWN_GOAL in kinds


def test_cutting_a_category_moves_the_date_earlier():
    """§7's feedback loop: cut travel by ₹1,000 and the date moves."""
    result = saving_if_reduced(WORKED, "travel", 1_000, TODAY)
    assert result is not None
    improved, days_earlier = result
    assert days_earlier > 0
    assert improved.target_date < build_plan(WORKED, TODAY).goal.target_date


def test_only_government_instruments_are_named_as_somewhere_to_park():
    """Standing constraint 4: never a fund, never a stock, never a policy."""
    names = " ".join(name for name, _ in build_plan(WORKED, TODAY).safe_parking).lower()
    for forbidden in ("mutual fund", "sip", "equity", "stock", "ulip", "nifty", "share"):
        assert forbidden not in names
    assert "recurring deposit" in names or "ppf" in names


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _png_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def test_a_surplus_renders_a_readable_png():
    png = render_chart(WORKED)
    assert png[:4] == b"\x89PNG"
    width, height = _png_size(png)
    assert width > 400 and height > 300


def test_a_deficit_switches_to_a_bar_chart():
    """§7: a pie cannot show a deficit."""
    deficit = Budget(income=15_000, categories={"rent": 7_000, "food": 5_000, "emi": 5_500})
    assert deficit.in_deficit
    pie = render_chart(WORKED)
    bars = render_chart(deficit)
    # Different shapes, so different figure sizes — the cheap structural check.
    assert _png_size(pie) != _png_size(bars)
    assert bars[:4] == b"\x89PNG"


def test_savings_has_its_own_colour_and_it_is_the_only_green():
    """Visually rewarding the thing the feature is trying to grow."""
    assert CATEGORY_COLOURS["savings"] == "#2F9E44"
    greens = [k for k, v in CATEGORY_COLOURS.items() if v == "#2F9E44"]
    assert greens == ["savings"]


def test_every_category_has_a_fixed_colour():
    """The same category must be the same colour in every chart the user ever sees."""
    for key in load_benchmarks().known_categories():
        assert key in CATEGORY_COLOURS


def test_a_devanagari_font_is_bundled():
    """⚠ matplotlib's default font renders Hindi as empty boxes, and it does so on
    the deploy box even when the local machine happened to have a system font."""
    assert devanagari_available(), "bundle a Devanagari font in data/fonts/"


def test_a_hindi_chart_renders_without_falling_over():
    png = render_chart(WORKED, language="hi")
    assert png[:4] == b"\x89PNG"


def test_a_zero_income_budget_does_not_divide_by_zero():
    empty = Budget(income=0, categories={"rent": 5_000})
    assert empty.share_of_income("rent") == 0.0
    assert empty.surplus_pct == 0.0


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


def test_the_response_has_every_section_from_the_plan():
    message = run_budget(WORKED, with_chart=True, today=TODAY)
    text = render_plain(message)
    assert "Your month" in text
    assert "Advice" in text
    assert "savings goal" in text.lower()
    assert len(message.images) == 1


def test_the_response_shows_each_category_with_its_share_and_verdict():
    text = render_plain(run_budget(WORKED, with_chart=False, today=TODAY))
    assert "₹8,000 — 32%" in text
    assert "slightly high" in text
    assert "₹4,000 — 16%" in text
    assert "healthy" in text


def test_the_response_names_the_goal_date():
    text = render_plain(run_budget(WORKED, with_chart=False, today=TODAY))
    goal = build_plan(WORKED, TODAY).goal
    assert goal.date_label in text
    assert "₹45,000" in text


def test_the_response_carries_its_disclaimer():
    text = render_plain(run_budget(WORKED, with_chart=False, today=TODAY))
    assert "not financial advice" in text


def test_the_feature_never_emits_channel_markup():
    for message in (
        run_budget(WORKED, with_chart=False, today=TODAY),
        format_needs_more("I could not read that."),
    ):
        text = render_plain(message)
        assert "<" not in text and "**" not in text


def test_an_ambiguous_line_gets_a_next_action_and_examples():
    """§8: never a naked error."""
    text = render_plain(format_needs_more("I could not find your income."))
    assert "income 25000 rent 8000" in text
    assert format_needs_more("x").keyboard is not None


def test_a_deficit_response_states_the_gap_plainly():
    deficit = Budget(income=15_000, categories={"rent": 7_000, "food": 5_000, "emi": 5_500})
    text = render_plain(run_budget(deficit, with_chart=False, today=TODAY))
    assert "more than you earn" in text
    assert "₹2,500" in text


def test_a_debt_trap_emi_is_called_out():
    """Above 35% is slide 4's threshold."""
    trapped = Budget(income=20_000, categories={"rent": 5_000, "food": 4_000, "emi": 8_000})
    advice = " ".join(analyse(trapped).advice)
    assert "35%" in advice
    assert "another loan" in advice


def test_under_spending_on_food_is_raised_not_praised():
    thin = Budget(income=20_000, categories={"rent": 6_000, "food": 2_000, "travel": 1_500})
    analysis = analyse(thin)
    assert any(v.key == "food" and v.band is CategoryBand.UNDER for v in analysis.verdicts)
    assert any("low" in point for point in analysis.advice)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_the_month_key_is_year_dash_month():
    assert current_month(date(2026, 8, 2)) == "2026-08"


def test_analyse_budget_is_pure_and_needs_no_database():
    analysis, plan = analyse_budget(WORKED, TODAY)
    assert analysis.verdicts
    assert plan.goal is not None


async def test_a_budget_is_saved_with_its_goal_and_date(session):
    from app.features.budget.service import check_budget_text
    from app.models import repo
    from app.models.db import Budget as BudgetRow
    from app.models.enums import Channel

    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="88")
    await check_budget_text(
        "income 25000 rent 8000 food 4000 travel 3000",
        session=session,
        user=user,
        today=TODAY,
    )

    row = session.query(BudgetRow).one()
    assert row.month == "2026-08"
    assert row.income == 25_000
    assert row.categories_json == {"rent": 8_000, "food": 4_000, "travel": 3_000}
    assert row.savings_goal == pytest.approx(45_000)
    assert row.target_date is not None


async def test_re_entering_the_month_replaces_it(session):
    from app.features.budget.service import check_budget_text
    from app.models import repo
    from app.models.db import Budget as BudgetRow
    from app.models.enums import Channel

    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="88")
    for line in (
        "income 25000 rent 8000 food 4000",
        "income 25000 rent 8500 food 4000",
    ):
        await check_budget_text(line, session=session, user=user, today=TODAY)

    row = session.query(BudgetRow).one()
    assert row.categories_json["rent"] == 8_500


async def test_an_ambiguous_line_is_not_persisted(session):
    from app.features.budget.service import check_budget_text
    from app.models import repo
    from app.models.db import Budget as BudgetRow
    from app.models.enums import Channel

    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="88")
    response = await check_budget_text(
        "income 25000 8000 4000", session=session, user=user, today=TODAY
    )

    assert "could not tell" in render_plain(response)
    assert session.query(BudgetRow).count() == 0
