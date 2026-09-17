"""Finance engine tests — hand-verified targets, no mocks, no fixtures, no network.

The plan's §12 bar: "Before any PDF is parsed, finance.py is tested standalone
against hand-verified amortisation schedules." This is that suite.

The canonical case throughout is the worked example from §5 of the implementation
plan: a Rs 1,00,000 personal loan over 24 months at 14% flat, with a 2.5%
processing fee plus 18% GST deducted from disbursal.
"""

import pytest

from app.features.loan.finance import (
    amortisation_schedule,
    effective_annual_rate,
    emi,
    emi_flat,
    flat_to_reducing,
    outstanding_balance,
    penalty_exposure,
    prepayment_cost,
    rate_from_emi,
    total_repayment,
    true_apr,
)

PRINCIPAL = 100_000
TENURE = 24
PROCESSING_FEE_WITH_GST = 2_950  # 2.5% of 1,00,000 = 2,500, +18% GST
NET_DISBURSED = PRINCIPAL - PROCESSING_FEE_WITH_GST  # Rs 97,050


# ---------------------------------------------------------------------------
# EMI
# ---------------------------------------------------------------------------


def test_emi_reducing_matches_plan_worked_example():
    """Rs 1,00,000 / 24 months / 14% reducing -> Rs 4,801/month."""
    assert emi(PRINCIPAL, 14, TENURE) == pytest.approx(4801, abs=1)


def test_emi_flat_matches_plan_worked_example():
    """Rs 1,00,000 / 24 months / 14% flat -> Rs 5,333/month."""
    assert emi_flat(PRINCIPAL, 14, TENURE) == pytest.approx(5333, abs=1)


def test_flat_costs_more_than_reducing_at_the_same_headline_rate():
    """The whole point of the feature, stated as an inequality."""
    assert emi_flat(PRINCIPAL, 14, TENURE) > emi(PRINCIPAL, 14, TENURE)


def test_emi_at_zero_rate_is_simple_division():
    assert emi(120_000, 0, 12) == pytest.approx(10_000)


def test_emi_single_month_repays_principal_plus_one_month_interest():
    result = emi(100_000, 12, 1)
    assert result == pytest.approx(101_000)


@pytest.mark.parametrize(
    ("principal", "rate", "months"),
    [(0, 14, 24), (-100, 14, 24), (100_000, 14, 0), (100_000, 14, -6), (100_000, -1, 24)],
)
def test_emi_rejects_impossible_inputs(principal, rate, months):
    """Bad input raises loudly. It never silently returns a number a user might see."""
    with pytest.raises(ValueError):
        emi(principal, rate, months)


# ---------------------------------------------------------------------------
# Flat -> reducing: the deception the product exists to expose
# ---------------------------------------------------------------------------


def test_flat_to_reducing_conversion():
    """14% flat over 24 months is really 24.92% on a reducing balance.

    NOTE — this contradicts the implementation plan, which states 25.3% in §5,
    §12 and the demo script. 25.3% is the plan's error, not this engine's:
    a genuine 25.3% reducing loan produces an EMI of Rs 5,352 and a total of
    Rs 1,28,453, which contradicts the plan's *own* stated EMI of Rs 5,333 and
    total of Rs 1,27,992. The 24.92% figure reproduces both exactly.
    See test_plan_25_3_percent_contradicts_the_plans_own_emi below.
    """
    assert flat_to_reducing(PRINCIPAL, 14, TENURE) == pytest.approx(24.92, abs=0.01)


def test_plan_25_3_percent_contradicts_the_plans_own_emi():
    """Guards the correction above, so nobody quietly reverts it to 25.3%."""
    emi_at_25_3 = emi(PRINCIPAL, 25.3, TENURE)
    assert emi_at_25_3 == pytest.approx(5352, abs=1)
    assert abs(emi_at_25_3 - 5333) > 15  # materially different from the plan's EMI


def test_flat_to_reducing_round_trips_through_emi():
    """Converting and re-pricing must land back on the same instalment."""
    flat_emi = emi_flat(PRINCIPAL, 14, TENURE)
    reducing = flat_to_reducing(PRINCIPAL, 14, TENURE)
    assert emi(PRINCIPAL, reducing, TENURE) == pytest.approx(flat_emi, abs=0.01)


@pytest.mark.parametrize("flat_rate", [8, 10, 12, 14, 18, 24])
def test_flat_always_converts_to_a_higher_reducing_rate(flat_rate):
    """A flat rate is never cheaper than the same number quoted reducing."""
    assert flat_to_reducing(PRINCIPAL, flat_rate, TENURE) > flat_rate


@pytest.mark.parametrize(
    ("months", "expected"),
    [
        (6, 23.617),
        (12, 24.909),
        (18, 25.062),  # the penalty peaks here, then declines
        (24, 24.924),
        (36, 24.402),
        (60, 23.248),
        (120, 21.010),
    ],
)
def test_flat_penalty_across_tenures(months, expected):
    """The flat-rate penalty is NOT monotonic in tenure.

    It peaks around 18 months and falls away on long loans. Worth pinning down,
    because the intuitive guess (longer loan = worse) is wrong, and a future
    refactor that "fixes" the curve to be monotonic would be introducing a bug.
    Each value is independently confirmed: discounting the flat EMI stream at the
    returned rate returns exactly the principal.
    """
    assert flat_to_reducing(PRINCIPAL, 14, months) == pytest.approx(expected, abs=0.01)


def test_flat_penalty_multiple_stays_in_the_familiar_band():
    """The rule of thumb lenders and borrowers both use: flat is ~1.5-1.8x reducing."""
    for months in (6, 12, 24, 36, 60, 120):
        multiple = flat_to_reducing(PRINCIPAL, 14, months) / 14
        assert 1.4 < multiple < 1.9


# ---------------------------------------------------------------------------
# Rate recovery
# ---------------------------------------------------------------------------


def test_rate_from_emi_inverts_emi():
    known_emi = emi(PRINCIPAL, 16.5, 36)
    assert rate_from_emi(PRINCIPAL, known_emi, 36) == pytest.approx(16.5, abs=1e-6)


def test_rate_from_emi_returns_zero_for_an_interest_free_loan():
    assert rate_from_emi(120_000, 10_000, 12) == pytest.approx(0.0)


def test_rate_from_emi_refuses_to_guess_beyond_solver_range():
    """Past 1200% p.a. the solver raises rather than pinning to its upper bound.

    A silently clamped rate would be a plausible-looking number with nothing behind
    it — exactly the failure mode this engine exists to prevent.
    """
    with pytest.raises(ValueError):
        rate_from_emi(100_000, 500_000, 24)


def test_effective_annual_rate_compounds_above_nominal():
    assert effective_annual_rate(24.92) == pytest.approx(27.98, abs=0.01)
    assert effective_annual_rate(0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# True APR — fees deducted from disbursal
# ---------------------------------------------------------------------------


def test_true_apr_matches_plan_worked_example():
    """Receiving Rs 97,050 but paying EMIs sized for Rs 1,00,000 -> 28.1% APR."""
    instalment = emi_flat(PRINCIPAL, 14, TENURE)
    assert true_apr(NET_DISBURSED, instalment, TENURE) == pytest.approx(28.1, abs=0.05)


def test_true_apr_exceeds_the_headline_reducing_rate():
    instalment = emi_flat(PRINCIPAL, 14, TENURE)
    headline = flat_to_reducing(PRINCIPAL, 14, TENURE)
    assert true_apr(NET_DISBURSED, instalment, TENURE) > headline


def test_true_apr_equals_reducing_rate_when_nothing_is_deducted():
    instalment = emi(PRINCIPAL, 14, TENURE)
    assert true_apr(PRINCIPAL, instalment, TENURE) == pytest.approx(14, abs=1e-6)


def test_bundled_insurance_pushes_apr_higher_still():
    instalment = emi_flat(PRINCIPAL, 14, TENURE)
    with_insurance = true_apr(NET_DISBURSED - 1_200, instalment, TENURE)
    assert with_insurance > true_apr(NET_DISBURSED, instalment, TENURE)


# ---------------------------------------------------------------------------
# Total repayment
# ---------------------------------------------------------------------------


def test_total_repayment_of_the_worked_example():
    instalment = emi_flat(PRINCIPAL, 14, TENURE)
    assert total_repayment(instalment, TENURE) == pytest.approx(128_000, abs=10)


def test_total_repayment_includes_one_time_charges():
    assert total_repayment(5_000, 24, one_time_charges=2_950) == pytest.approx(122_950)


# ---------------------------------------------------------------------------
# Amortisation
# ---------------------------------------------------------------------------


def test_schedule_has_one_row_per_month():
    assert len(amortisation_schedule(PRINCIPAL, 14, TENURE)) == TENURE


def test_schedule_fully_repays_the_principal():
    schedule = amortisation_schedule(PRINCIPAL, 14, TENURE)
    assert schedule[-1].closing_balance == pytest.approx(0.0, abs=0.01)
    assert sum(row.principal for row in schedule) == pytest.approx(PRINCIPAL, abs=0.01)


def test_schedule_interest_matches_total_less_principal():
    schedule = amortisation_schedule(PRINCIPAL, 14, TENURE)
    total_paid = sum(row.payment for row in schedule)
    total_interest = sum(row.interest for row in schedule)
    assert total_paid - PRINCIPAL == pytest.approx(total_interest, abs=0.01)


def test_interest_share_falls_every_month():
    """A reducing-balance loan front-loads interest. If this inverts, the maths is wrong."""
    schedule = amortisation_schedule(PRINCIPAL, 14, TENURE)
    interests = [row.interest for row in schedule]
    assert all(a > b for a, b in zip(interests, interests[1:], strict=False))


def test_first_month_interest_is_one_month_of_the_rate():
    schedule = amortisation_schedule(PRINCIPAL, 14, TENURE)
    assert schedule[0].interest == pytest.approx(PRINCIPAL * 0.14 / 12)


def test_outstanding_balance_endpoints():
    assert outstanding_balance(PRINCIPAL, 14, TENURE, 0) == PRINCIPAL
    assert outstanding_balance(PRINCIPAL, 14, TENURE, TENURE) == 0.0


def test_outstanding_balance_decreases_monotonically():
    balances = [outstanding_balance(PRINCIPAL, 14, TENURE, m) for m in range(TENURE + 1)]
    assert all(a > b for a, b in zip(balances, balances[1:], strict=False))


# ---------------------------------------------------------------------------
# Penalty exposure
# ---------------------------------------------------------------------------


def test_one_missed_emi_matches_plan_worked_example():
    """2% per month on overdue + Rs 500 bounce -> about Rs 607."""
    instalment = emi_flat(PRINCIPAL, 14, TENURE)
    exposure = penalty_exposure(instalment, penalty_pct_per_month=2, bounce_charge=500)
    assert exposure.total == pytest.approx(607, abs=1)


def test_penalty_breakdown_sums_to_total():
    exposure = penalty_exposure(5_333.33, 2, bounce_charge=500, loan_annual_rate_pct=24.92)
    assert sum(exposure.components.values()) == pytest.approx(exposure.total)


def test_interest_on_overdue_is_opt_in():
    """Default matches the document; the extra term is only added when asked for."""
    base = penalty_exposure(5_333.33, 2, bounce_charge=500)
    with_interest = penalty_exposure(5_333.33, 2, bounce_charge=500, loan_annual_rate_pct=24.92)
    assert base.extra_loan_interest == 0.0
    assert with_interest.total > base.total


def test_two_missed_emis_cost_more_than_one():
    one = penalty_exposure(5_333.33, 2, bounce_charge=500, months_overdue=1)
    two = penalty_exposure(5_333.33, 2, bounce_charge=500, months_overdue=2)
    assert two.penalty_interest > one.penalty_interest


# ---------------------------------------------------------------------------
# Prepayment
# ---------------------------------------------------------------------------


def test_prepayment_blocked_during_lock_in():
    """Silence about a lock-in is a finding; so is the lock-in itself."""
    result = prepayment_cost(
        PRINCIPAL, 24.92, TENURE, prepay_after_months=3, charge_pct=4, lock_in_months=6
    )
    assert result.allowed is False
    assert result.months_until_allowed == 3
    assert result.reason is not None


def test_prepayment_allowed_after_lock_in():
    result = prepayment_cost(
        PRINCIPAL, 24.92, TENURE, prepay_after_months=6, charge_pct=4, lock_in_months=6
    )
    assert result.allowed is True
    assert result.charge == pytest.approx(result.outstanding * 0.04)


def test_prepayment_charge_shrinks_as_the_loan_is_repaid():
    early = prepayment_cost(PRINCIPAL, 24.92, TENURE, 6, charge_pct=4)
    late = prepayment_cost(PRINCIPAL, 24.92, TENURE, 18, charge_pct=4)
    assert early.charge > late.charge
