"""Loan analysis: computation, hidden charges, advice, and the §5 worked example.

The worked-example test is the important one. §5 states eleven figures for a
₹1,00,000 / 24-month / 14% flat loan, and every one of them is reproduced here to
the rupee — except 25.3%, which the plan gets wrong and Phase 1 already documented.

Everything else guards a specific way this feature could quietly lie: guessing a
field the document did not state, assuming a rate basis that was never disclosed,
or reporting an EMI that does not match the terms it was derived from.
"""

from __future__ import annotations

import pytest

from app.channels.base import render_plain
from app.features.loan.advisor import Verdict, load_benchmarks
from app.features.loan.computed import compute, penalty_pct_per_month
from app.features.loan.formatter import format_analysis, format_unreadable
from app.features.loan.hidden import Severity, detect
from app.features.loan.schema import LoanTerms
from app.features.loan.service import analyse_terms

# The §5 worked example, exactly.
WORKED_EXAMPLE = LoanTerms(
    lender_name="Example Finance Ltd",
    loan_type="personal",
    principal=100_000,
    tenure_months=24,
    interest_rate=14.0,
    rate_basis="flat",
    stated_emi=5333,
    processing_fee=2.5,
    processing_fee_is_pct=True,
    gst_on_fees=True,
    insurance_premium=1200,
    prepayment_charge=4.0,
    foreclosure_lock_in=6,
    late_payment_penalty="2% per month on the overdue amount",
    bounce_charge=500,
)


def finding_ids(terms: LoanTerms) -> set[str]:
    return {finding.id for finding in detect(compute(terms))}


# ---------------------------------------------------------------------------
# §5's worked example, figure by figure
# ---------------------------------------------------------------------------


def test_the_flat_rate_converts_to_the_corrected_reducing_rate():
    """24.92%, not the 25.3% the plan states — see the correction in BUILD_PLAN."""
    computed = compute(WORKED_EXAMPLE)
    assert computed.effective_reducing_rate == pytest.approx(24.92, abs=0.01)


def test_the_computed_emi_matches_the_document():
    computed = compute(WORKED_EXAMPLE)
    assert computed.computed_emi == 5333
    assert computed.emi_matches_stated is True


def test_the_total_repaid_matches_the_plan():
    assert compute(WORKED_EXAMPLE).total_repaid == pytest.approx(127_992, abs=1)


def test_the_printed_arithmetic_checks_out():
    """A judge who multiplies the EMI we show by the tenure we show must get our total."""
    computed = compute(WORKED_EXAMPLE)
    assert computed.computed_emi * WORKED_EXAMPLE.tenure_months == computed.total_repaid


def test_the_fee_and_gst_match_the_plan():
    computed = compute(WORKED_EXAMPLE)
    assert computed.upfront_fees == pytest.approx(2_950, abs=1)
    assert dict(computed.fee_breakdown)["GST at 18% on the above"] == pytest.approx(450, abs=1)


def test_the_net_disbursal_matches_the_plan():
    assert compute(WORKED_EXAMPLE).net_disbursed == pytest.approx(97_050, abs=1)


def test_the_true_apr_matches_the_plan():
    assert compute(WORKED_EXAMPLE).true_apr_pct == pytest.approx(28.1, abs=0.05)


def test_one_missed_emi_matches_the_plan():
    assert compute(WORKED_EXAMPLE).penalty.total == pytest.approx(607, abs=1)


def test_insurance_is_reported_separately_from_the_disbursal_deduction():
    """§5 puts the net at ₹97,050 and flags the ₹1,200 premium on its own line."""
    computed = compute(WORKED_EXAMPLE)
    assert computed.bundled_insurance == 1_200
    assert computed.net_disbursed == pytest.approx(97_050, abs=1)
    assert "bundled_insurance" in finding_ids(WORKED_EXAMPLE)


def test_the_worked_example_produces_the_plans_hidden_charges():
    assert {
        "flat_rate_presented_as_comparable",
        "fees_deducted_from_disbursal",
        "bundled_insurance",
        "foreclosure_lock_in",
    } <= finding_ids(WORKED_EXAMPLE)


def test_the_worked_example_is_expensive_but_not_predatory():
    """§5's own verdict, in §5's own words."""
    computed, findings, advice = analyse_terms(WORKED_EXAMPLE)
    assert advice.verdict is Verdict.EXPENSIVE
    assert "not predatory" in advice.headline


# ---------------------------------------------------------------------------
# The rate basis — the field that decides everything below it
# ---------------------------------------------------------------------------


def test_a_reducing_rate_is_used_as_stated():
    terms = LoanTerms(principal=100_000, tenure_months=24, interest_rate=14, rate_basis="reducing")
    computed = compute(terms)
    assert computed.effective_reducing_rate == 14
    assert computed.computed_emi == 4801  # the §5 / Phase 1 figure


def test_a_flat_loan_costs_far_more_than_the_same_headline_reducing_loan():
    """The deception the feature exists to expose."""
    flat = compute(
        LoanTerms(principal=100_000, tenure_months=24, interest_rate=14, rate_basis="flat")
    )
    reducing = compute(
        LoanTerms(principal=100_000, tenure_months=24, interest_rate=14, rate_basis="reducing")
    )
    assert flat.total_repaid - reducing.total_repaid == pytest.approx(12_768, abs=5)


def test_an_undisclosed_basis_is_worked_back_from_the_emi_not_assumed():
    """The EMI is a fact; the basis is not. Derive from the fact."""
    terms = LoanTerms(
        principal=100_000, tenure_months=24, interest_rate=14, rate_basis="undisclosed",
        stated_emi=5333,
    )
    computed = compute(terms)
    assert computed.effective_reducing_rate == pytest.approx(24.92, abs=0.05)
    assert "worked back" in computed.rate_source


def test_an_undisclosed_basis_with_no_emi_refuses_to_guess():
    """Assuming "reducing" flatters a document that is hiding something."""
    terms = LoanTerms(
        principal=100_000, tenure_months=24, interest_rate=14, rate_basis="undisclosed"
    )
    computed = compute(terms)
    assert computed.effective_reducing_rate is None
    assert computed.uncomputable
    assert "flat or reducing" in computed.uncomputable[0]


def test_an_undisclosed_basis_is_a_red_flag_in_its_own_right():
    """§5: "Silence is a finding." RBI requires the effective rate to be disclosed."""
    terms = LoanTerms(
        principal=100_000, tenure_months=24, interest_rate=14, rate_basis="undisclosed"
    )
    findings = detect(compute(terms))
    flag = next(f for f in findings if f.id == "rate_basis_undisclosed")
    assert flag.severity is Severity.SERIOUS
    assert "RBI" in flag.sentence


# ---------------------------------------------------------------------------
# Silence as a finding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("missing_field", "expected_finding"),
    [
        ("prepayment_charge", "missing_prepayment_charge"),
        ("late_payment_penalty", "missing_late_payment_penalty"),
        ("processing_fee", "missing_processing_fee"),
    ],
)
def test_a_missing_clause_is_reported_rather_than_skipped(missing_field, expected_finding):
    terms = WORKED_EXAMPLE.model_copy(update={missing_field: None})
    assert expected_finding in finding_ids(terms)


def test_an_unattached_schedule_of_charges_is_a_finding():
    terms = WORKED_EXAMPLE.model_copy(update={"references_external_schedule": True})
    assert "external_schedule_of_charges" in finding_ids(terms)


def test_unmentioned_gst_is_flagged_because_it_will_still_be_charged():
    terms = WORKED_EXAMPLE.model_copy(update={"gst_on_fees": False})
    assert "gst_not_mentioned" in finding_ids(terms)


def test_missing_fields_are_listed_by_the_schema():
    sparse = LoanTerms(principal=50_000)
    missing = sparse.missing_fields
    assert "tenure_months" in missing and "interest_rate" in missing
    assert "principal" not in missing
    # Booleans have real defaults and are not "missing".
    assert "gst_on_fees" not in missing


# ---------------------------------------------------------------------------
# The deliberately incomplete document — Gate 4's third case
# ---------------------------------------------------------------------------


def test_an_incomplete_document_reports_what_is_missing_instead_of_guessing():
    sparse = LoanTerms(loan_type="personal", principal=50_000, tenure_months=12)
    computed, findings, advice = analyse_terms(sparse)

    assert computed.computed_emi is None
    assert computed.uncomputable
    assert advice.verdict is Verdict.UNKNOWN

    text = render_plain(format_analysis(computed, findings, advice))
    assert "not going to guess" in text
    assert "What is missing" in text
    assert "Ask the lender for, in writing" in text


def test_an_incomplete_document_never_prints_an_invented_number():
    sparse = LoanTerms(loan_type="personal", principal=50_000, tenure_months=12)
    computed, findings, advice = analyse_terms(sparse)
    text = render_plain(format_analysis(computed, findings, advice))

    assert "EMI" not in text.split("What is missing")[0].replace("Loan Analysis", "")
    assert "% per year" not in text


def test_a_document_with_no_principal_cannot_be_costed():
    computed = compute(LoanTerms(loan_type="personal", tenure_months=24, interest_rate=14))
    assert not computed.has_numbers
    assert any("principal" in reason for reason in computed.uncomputable)


# ---------------------------------------------------------------------------
# The EMI cross-check
# ---------------------------------------------------------------------------


def test_a_stated_emi_that_does_not_match_the_terms_is_surfaced():
    """§5: "A mismatch means undisclosed costs are baked in."."""
    terms = LoanTerms(
        principal=100_000, tenure_months=24, interest_rate=14, rate_basis="reducing",
        stated_emi=5_600,  # the terms give 4,801
    )
    computed = compute(terms)
    assert computed.emi_matches_stated is False
    flag = next(f for f in detect(computed) if f.id == "emi_mismatch")
    assert flag.severity is Severity.SERIOUS


def test_the_emi_check_tolerates_a_rupee_of_lender_rounding():
    terms = LoanTerms(
        principal=100_000, tenure_months=24, interest_rate=14, rate_basis="reducing",
        stated_emi=4_802,
    )
    assert compute(terms).emi_matches_stated is True


# ---------------------------------------------------------------------------
# Penalty parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2% per month on the overdue amount", 2.0),
        ("2.5% p.m. on overdue", 2.5),
        ("24% per annum on overdue instalments", 2.0),
        ("36% p.a. penal interest", 3.0),
        ("penalty as per schedule of charges", None),
        ("", None),
        (None, None),
    ],
)
def test_the_penalty_rate_is_read_from_the_documents_own_words(text, expected):
    result = penalty_pct_per_month(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=0.01)


def test_an_unparseable_penalty_produces_no_invented_figure():
    terms = WORKED_EXAMPLE.model_copy(
        update={"late_payment_penalty": "as per schedule", "bounce_charge": None}
    )
    assert compute(terms).penalty is None


# ---------------------------------------------------------------------------
# Advice and benchmarking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("apr", "verdict"),
    [
        (9.0, Verdict.CHEAPER_THAN_BANKS),
        (14.0, Verdict.BANK_RATE),
        (28.0, Verdict.EXPENSIVE),
        (35.0, Verdict.VERY_EXPENSIVE),
        (55.0, Verdict.PREDATORY),
    ],
)
def test_a_loan_is_placed_against_the_market(apr, verdict):
    from app.features.loan.advisor import classify

    benchmarks = load_benchmarks()
    assert classify(apr, benchmarks.bands_for("personal"), benchmarks.informal) is verdict


def test_the_advice_never_names_a_lender_or_a_product():
    """Standing constraint 4: categories only, never a named product."""
    _, findings, advice = analyse_terms(WORKED_EXAMPLE)
    everything = " ".join(advice.negotiation_points) + advice.headline + advice.comparison
    for brand in ("HDFC", "SBI", "Bajaj", "Zerodha", "Groww", "PolicyBazaar", "ICICI"):
        assert brand.lower() not in everything.lower()


def test_negotiation_points_carry_the_rupee_figure_that_makes_them_leverage():
    _, _, advice = analyse_terms(WORKED_EXAMPLE)
    joined = " ".join(advice.negotiation_points)
    assert "₹1,200" in joined  # the insurance worth reclaiming
    assert "₹2,950" in joined  # the fee worth negotiating


def test_the_verification_date_is_shown_with_every_comparison():
    _, _, advice = analyse_terms(WORKED_EXAMPLE)
    assert advice.last_reviewed
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    assert advice.last_reviewed in text


def test_an_uncostable_loan_still_gets_a_bank_comparison_to_aim_at():
    _, _, advice = analyse_terms(LoanTerms(loan_type="personal", principal=50_000))
    assert advice.verdict is Verdict.UNKNOWN
    assert "11-16%" in advice.comparison


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


def test_the_response_has_all_six_sections():
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    for section in ("INTEREST", "EMI", "FEES", "PENALTY", "HIDDEN CHARGES", "ADVICE"):
        assert section in text


def test_the_sections_appear_in_the_order_a_borrower_asks_them():
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    positions = [
        text.index(section)
        for section in ("INTEREST", "📅 EMI", "FEES", "PENALTY", "HIDDEN CHARGES", "ADVICE")
    ]
    assert positions == sorted(positions)


def test_the_response_tells_the_user_what_actually_reaches_their_account():
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    assert "You will receive ₹97,050 in your account, not ₹1,00,000" in text


def test_the_response_states_the_sentence_that_changes_behaviour():
    """§5: "You borrow X and repay Y" is the line that lands."""
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    assert "You borrow ₹97,050 and repay ₹1,27,992" in text


def test_the_response_carries_its_disclaimer():
    text = render_plain(format_analysis(*analyse_terms(WORKED_EXAMPLE)))
    assert "Verify all terms with the lender before signing" in text


def test_the_feature_never_emits_channel_markup():
    for message in (
        format_analysis(*analyse_terms(WORKED_EXAMPLE)),
        format_analysis(*analyse_terms(LoanTerms(principal=50_000))),
        format_unreadable(),
    ):
        text = render_plain(message)
        assert "<" not in text and "**" not in text


def test_an_unreadable_pdf_gets_three_things_to_try():
    text = render_plain(format_unreadable())
    assert "could not read that document" in text
    assert "my problem, not yours" in text
    assert format_unreadable().keyboard is not None


def test_findings_are_ranked_worst_first():
    findings = detect(compute(WORKED_EXAMPLE))
    severities = [int(f.severity) for f in findings]
    assert severities == sorted(severities, reverse=True)
