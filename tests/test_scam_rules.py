"""Rule engine and scorer tests.

Two halves, and the second is the one that decides whether this product is
shippable. Every rule must fire on the thing it exists to catch — but §13's bar is
that **no genuine message scores above 50**, and a false positive on a real bank SMS
is not a smaller version of a miss. It teaches the user we cry wolf, and the next
warning is the one that mattered.

So `GENUINE_MESSAGES` below is a standing regression set. It is not the 30-fixture
library from Phase 0.4 — that is real screenshots and human work — but it is the
same bar applied to the message types that actually arrive most often in India.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.features.scam.rules import (
    MAX_RULE_SCORE,
    evaluate,
    load_rules,
    rule_score,
)
from app.features.scam.schema import ExtractedMessage
from app.features.scam.scorer import (
    FALSE_POSITIVE_CEILING,
    Band,
    band_for,
    score,
)


def msg(text: str, **kwargs) -> ExtractedMessage:
    return ExtractedMessage(message_text=text, **kwargs)


def fired_ids(message: ExtractedMessage) -> set[str]:
    return {f.id for f in evaluate(message)}


# ---------------------------------------------------------------------------
# The rule file itself
# ---------------------------------------------------------------------------


def test_all_fourteen_rules_from_the_plan_are_present():
    assert len(load_rules()) == 14


def test_the_weights_match_the_plan():
    """§4's table, to the point. Tuning these is a data change; changing them by
    accident is a scoring bug nobody notices."""
    expected = {
        "guaranteed_return": 25,
        "implausible_return": 20,
        "no_registration": 18,
        "manufactured_urgency": 15,
        "personal_payment_handle": 15,
        "otp_or_remote_access": 25,
        "recruitment_mechanics": 12,
        "shortened_or_lookalike_link": 12,
        "fake_authority": 14,
        "celebrity_endorsement": 10,
        "advance_fee": 18,
        "job_task_scam": 16,
        "loan_app_coercion": 18,
        "unsolicited_contact": 8,
    }
    assert {r.id: r.weight for r in load_rules()} == expected


def test_every_rule_can_explain_itself_to_a_user():
    for rule in load_rules():
        assert rule.sentence, f"{rule.id} has no user-facing sentence"
        assert len(rule.sentence) > 60, f"{rule.id}'s sentence is too thin to teach anything"


def test_every_rule_has_a_way_to_fire():
    for rule in load_rules():
        assert rule.patterns or rule.check, f"{rule.id} can never fire"


# ---------------------------------------------------------------------------
# Each rule fires on what it exists to catch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule_id", "message"),
    [
        ("guaranteed_return", msg("100% guaranteed profit every month, no risk at all")),
        ("guaranteed_return", msg("पक्का मुनाफा, गारंटी के साथ")),
        ("implausible_return", msg("Double your money in 30 days")),
        ("implausible_return", msg("Earn 5% daily profit")),
        ("manufactured_urgency", msg("Only 3 seats left, offer closes in 2 hours")),
        ("manufactured_urgency", msg("जल्दी करें, सीमित समय")),
        ("otp_or_remote_access", msg("Please share the OTP you received to verify")),
        ("otp_or_remote_access", msg("Download AnyDesk and give me the code")),
        ("otp_or_remote_access", msg("OTP बताइए तुरंत")),
        ("recruitment_mechanics", msg("Add 5 friends to the group and earn commission")),
        ("recruitment_mechanics", msg("Refer and earn on every level income")),
        ("celebrity_endorsement", msg("Ratan Tata has invested in this platform")),
        ("advance_fee", msg("You have won ₹10 lakh. Pay ₹2,000 processing fee to claim")),
        ("advance_fee", msg("Send a small refundable deposit to receive your prize")),
        ("job_task_scam", msg("Work from home, like videos, earn ₹3000 daily")),
        ("job_task_scam", msg("Simple hotel review task, daily payout of Rs 1500")),
        ("loan_app_coercion", msg("Instant loan in 5 minutes, no documents needed")),
        ("loan_app_coercion", msg("Allow access to your contact list to continue")),
        ("loan_app_coercion", msg("We will inform all your contacts about your default")),
        ("fake_authority", msg("This is an RBI approved investment plan")),
    ],
)
def test_a_rule_fires_on_its_own_pattern(rule_id, message):
    assert rule_id in fired_ids(message)


def test_hindi_and_hinglish_are_covered_not_just_english():
    """Slide 9's segments do not type in English."""
    hindi = msg("गारंटी के साथ पक्का मुनाफा, जल्दी करें, सीमित समय")
    assert {"guaranteed_return", "manufactured_urgency"} <= fired_ids(hindi)

    hinglish = msg("Ghar baithe kamai karo, daily payout of Rs 2000")
    assert "job_task_scam" in fired_ids(hinglish)


# ---------------------------------------------------------------------------
# Field checks — what a regex cannot see
# ---------------------------------------------------------------------------


def test_a_return_is_judged_after_annualising_the_stated_period():
    monthly = msg("Great returns", claimed_return_pct=5.0, claimed_period_days=30)
    assert "implausible_return" in fired_ids(monthly)  # 5%/month is 61% a year


def test_a_realistic_annual_return_does_not_fire():
    """12% a year is what a good fund does. Flagging it would flag every real product."""
    honest = msg("Historical returns around 12% per annum", claimed_return_pct=12.0,
                 claimed_period_days=365)
    assert "implausible_return" not in fired_ids(honest)


def test_a_bare_percentage_with_no_period_is_read_as_annual():
    assert "implausible_return" not in fired_ids(msg("about 11% returns", claimed_return_pct=11.0))
    assert "implausible_return" in fired_ids(msg("40% returns", claimed_return_pct=40.0))


def test_a_missing_registration_number_is_itself_the_finding():
    """Silence is the red flag — the model reports null and the rule reads it."""
    claiming = msg(
        "I am a SEBI registered investment advisor",
        claimed_entity="SEBI registered advisor",
    )
    assert "no_registration" in fired_ids(claiming)


def test_showing_a_registration_number_clears_the_rule():
    honest = msg(
        "SEBI registered investment adviser INA000012345",
        claimed_entity="SEBI registered adviser",
        registration_number="INA000012345",
    )
    assert "no_registration" not in fired_ids(honest)


def test_a_personal_upi_handle_is_flagged():
    assert "personal_payment_handle" in fired_ids(
        msg("Pay here", payment_handles=["rajesh.k@okaxis"])
    )


def test_a_business_looking_handle_is_not_flagged():
    """A false positive here accuses a real merchant of fraud."""
    assert "personal_payment_handle" not in fired_ids(
        msg("Pay here", payment_handles=["bigbazaarstore@paytm"])
    )


def test_a_link_shortener_is_flagged_because_it_hides_the_destination():
    assert "shortened_or_lookalike_link" in fired_ids(
        msg("Click here", links=["https://bit.ly/3xKfPq"])
    )


@pytest.mark.parametrize("lookalike", ["hdfcbamk.com", "hdfcbank.co", "icicibanc.com"])
def test_a_lookalike_bank_domain_is_caught(lookalike):
    """One changed letter is the entire trick."""
    fired = evaluate(msg("Login now", links=[f"https://{lookalike}/login"]))
    flag = next(f for f in fired if f.id == "shortened_or_lookalike_link")
    assert "near-copy" in flag.evidence


def test_the_real_bank_domain_is_not_flagged_as_a_copy_of_itself():
    assert "shortened_or_lookalike_link" not in fired_ids(
        msg("Login", links=["https://www.hdfcbank.com/personal"])
    )


def test_an_authority_claim_backed_by_a_gov_link_is_not_fake():
    assert "fake_authority" not in fired_ids(
        msg("Government of India scheme details", links=["https://pmkisan.gov.in"])
    )


def test_unsolicited_contact_stays_narrow_on_purpose():
    """8 points is not worth teaching a user to distrust every unknown sender."""
    assert "unsolicited_contact" not in fired_ids(msg("Hello, are you there?"))


# ---------------------------------------------------------------------------
# §13 — the false-positive bar
# ---------------------------------------------------------------------------

GENUINE_MESSAGES = [
    pytest.param(
        msg(
            "Dear Customer, Rs.5,000.00 has been debited from A/c XX1234 on 02-08-26 to "
            "VPA merchant@okhdfcbank. Never share your OTP or PIN with anyone. Not you? "
            "Call 18001234567. -SBI",
            sender_name="AD-SBIINB",
        ),
        id="bank_debit_alert_that_warns_about_otp",
    ),
    pytest.param(
        msg(
            "123456 is your OTP for login. Valid for 10 minutes. Do not share this code "
            "with anyone, including bank staff.",
            sender_name="VM-HDFCBK",
        ),
        id="genuine_otp_message",
    ),
    pytest.param(
        msg(
            "Your electricity bill of Rs 1,240 is due on 15-08-2026. Pay at "
            "https://bescom.karnataka.gov.in to avoid disconnection.",
            links=["https://bescom.karnataka.gov.in"],
        ),
        id="utility_bill_with_a_real_due_date",
    ),
    pytest.param(
        msg(
            "PM-KISAN: Rs 2,000 has been credited to your account under the 16th "
            "instalment. Check status at pmkisan.gov.in",
            links=["https://pmkisan.gov.in"],
        ),
        id="real_government_scheme_credit",
    ),
    pytest.param(
        msg(
            "Your SIP of Rs 5,000 in HDFC Balanced Advantage Fund has been processed. "
            "Mutual fund investments are subject to market risks.",
            sender_name="HDFCMF",
        ),
        id="genuine_mutual_fund_confirmation",
    ),
    pytest.param(
        msg(
            "Your order has been shipped and will arrive by Tuesday. Track it in the app.",
            sender_name="Amazon",
        ),
        id="delivery_notification",
    ),
    pytest.param(
        msg(
            "Reminder: your LIC premium of Rs 8,450 is due on 20-08-2026. Pay online at "
            "licindia.in or at any branch.",
        ),
        id="insurance_premium_due",
    ),
    pytest.param(
        msg("आपके खाते से 2,000 रुपये निकाले गए हैं। OTP कभी किसी को न बताएं।"),
        id="hindi_bank_alert_warning_about_otp",
    ),
    pytest.param(
        msg(
            "Fixed Deposit booked. Rs 1,00,000 at 7.1% per annum for 24 months. "
            "Maturity 02-08-2028.",
            claimed_return_pct=7.1,
            claimed_period_days=365,
        ),
        id="genuine_fixed_deposit",
    ),
    pytest.param(
        msg(
            "Salary of Rs 32,450 credited to your account XX4421 for July 2026.",
            sender_name="AX-PAYROLL",
        ),
        id="salary_credit",
    ),
]


@pytest.mark.parametrize("message", GENUINE_MESSAGES)
def test_no_genuine_message_scores_above_fifty(message):
    """§13's hard bar. A failure here is a shipping blocker, not a tuning note."""
    assessment = score(evaluate(message), llm_score=None)
    assert assessment.score <= FALSE_POSITIVE_CEILING, (
        f"genuine message scored {assessment.score}: "
        f"{[f.id for f in assessment.fired]}"
    )


@pytest.mark.parametrize("message", GENUINE_MESSAGES)
def test_no_genuine_message_survives_even_a_confident_wrong_model(message):
    """The model is allowed to be wrong. It must not be allowed to be decisive."""
    assessment = score(evaluate(message), llm_score=100)
    assert assessment.band is not Band.ALMOST_CERTAINLY_SCAM


def test_a_bank_warning_not_to_share_an_otp_scores_zero_on_the_otp_rule():
    """The single most common message in India, on the single heaviest rule."""
    bank = msg("Never share your OTP or PIN with anyone, including bank staff.")
    assert "otp_or_remote_access" not in fired_ids(bank)
    assert rule_score(evaluate(bank)) == 0


# ---------------------------------------------------------------------------
# Scoring mechanics
# ---------------------------------------------------------------------------


def test_the_rule_score_is_the_capped_sum_of_weights():
    everything = msg(
        "100% guaranteed profit! Double your money! Only 2 seats left! "
        "Share your OTP! Refer and earn! RBI approved! Ratan Tata invested! "
        "Pay processing fee! Work from home daily payout! Instant loan no documents!"
    )
    assert rule_score(evaluate(everything)) == MAX_RULE_SCORE


def test_a_rule_contributes_its_weight_once_however_many_ways_it_matches():
    once = msg("guaranteed returns")
    many = msg("guaranteed returns, assured profit, 100% safe, no risk, fixed profit")
    assert rule_score(evaluate(once)) == rule_score(evaluate(many)) == 25


def test_reasons_are_ranked_heaviest_first():
    fired = evaluate(
        msg("Only 2 seats left! 100% guaranteed profit! Ratan Tata invested!")
    )
    weights = [f.weight for f in fired]
    assert weights == sorted(weights, reverse=True)


def test_the_evidence_is_quoted_back_in_the_users_sentence():
    fired = evaluate(msg("we offer 100% guaranteed profit to members"))
    flag = next(f for f in fired if f.id == "guaranteed_return")
    assert "{match}" not in flag.user_sentence
    assert "guaranteed profit" in flag.user_sentence


@pytest.mark.parametrize(
    ("value", "band"),
    [
        (0, Band.LIKELY_SAFE),
        (25, Band.LIKELY_SAFE),
        (26, Band.BE_CAREFUL),
        (50, Band.BE_CAREFUL),
        (51, Band.HIGH_RISK),
        (75, Band.HIGH_RISK),
        (76, Band.ALMOST_CERTAINLY_SCAM),
        (100, Band.ALMOST_CERTAINLY_SCAM),
    ],
)
def test_band_boundaries_match_the_plan(value, band):
    assert band_for(value) is band


def test_the_lowest_band_never_says_safe():
    """BUILD_PLAN standing constraint: "no major red flags found", never "safe"."""
    headline = Band.LIKELY_SAFE.headline.lower()
    assert "safe" not in headline and "genuine" not in headline
    assert "no major red flags" in headline


def test_the_blend_is_sixty_forty():
    fired = evaluate(msg("100% guaranteed profit"))  # 25
    assessment = score(fired, llm_score=75)
    assert assessment.score == round(0.6 * 25 + 0.4 * 75)  # 45


def test_without_a_model_opinion_the_rules_stand_alone():
    """An outage degrades the product; it does not stop it."""
    assessment = score(evaluate(msg("100% guaranteed profit")), llm_score=None)
    assert assessment.score == 25
    assert assessment.llm_used is False


# ---------------------------------------------------------------------------
# The two guardrails
# ---------------------------------------------------------------------------


def test_a_large_disagreement_downgrades_instead_of_averaging():
    fired = evaluate(msg("100% guaranteed profit"))  # rules = 25
    assessment = score(fired, llm_score=90)  # gap = 65
    assert assessment.band is Band.NEEDS_REVIEW
    assert assessment.downgraded
    assert "disagree by 65" in assessment.downgrade_reason


def test_a_disagreement_exactly_at_the_threshold_is_not_downgraded():
    fired = evaluate(msg("100% guaranteed profit"))  # 25
    assessment = score(fired, llm_score=65, settings=Settings(scam_disagreement_downgrade=40))
    assert not assessment.downgraded


def test_one_rule_alone_cannot_be_called_a_scam():
    """§13's floor. One 25-point rule plus an enthusiastic model is a suspicion,
    not a verdict, however loudly the model asserts otherwise."""
    fired = evaluate(msg("Please share the OTP"))
    assert len(fired) == 1

    for llm_opinion in (60, 80, 95, 100):
        assessment = score(fired, llm_score=llm_opinion)
        assert assessment.band is not Band.ALMOST_CERTAINLY_SCAM
        assert assessment.band is not Band.HIGH_RISK


def test_the_cap_holds_when_a_single_heavy_rule_would_clear_fifty():
    from app.features.scam.rules import FiredRule

    single = [FiredRule("x", "Heavy", 90, "evidence", "A sentence about it.")]
    assessment = score(single, llm_score=90)
    assert assessment.capped is True
    assert assessment.score == FALSE_POSITIVE_CEILING
    assert assessment.band is Band.BE_CAREFUL


def test_two_independent_rules_are_allowed_above_fifty():
    from app.features.scam.rules import FiredRule

    pair = [
        FiredRule("a", "One", 45, "e", "A sentence about it."),
        FiredRule("b", "Two", 45, "e", "Another sentence about it."),
    ]
    assessment = score(pair, llm_score=90)
    assert assessment.capped is False
    assert assessment.score > FALSE_POSITIVE_CEILING


def test_the_worked_example_from_the_plan_lands_in_the_top_band():
    """§4's screenshot: guaranteed 40% monthly, SEBI claim with no number,
    10 seats closing at 6 PM, payment to a personal UPI."""
    example = ExtractedMessage(
        message_text=(
            "🔥 GUARANTEED 40% monthly return! SEBI registered expert. Only 10 seats "
            "left, joining closes 6 PM today. Pay ₹5,000 to UPI rajesh.k@okaxis"
        ),
        claimed_entity="SEBI registered expert",
        claimed_return_pct=40.0,
        claimed_period_days=30,
        payment_handles=["rajesh.k@okaxis"],
    )
    fired = evaluate(example)
    assessment = score(fired, llm_score=95)

    assert {
        "guaranteed_return",
        "implausible_return",
        "no_registration",
        "manufactured_urgency",
        "personal_payment_handle",
    } <= {f.id for f in fired}
    assert assessment.band is Band.ALMOST_CERTAINLY_SCAM
    assert assessment.score >= 90


# ---------------------------------------------------------------------------
# Regressions found by running the live pipeline against real-looking messages
# ---------------------------------------------------------------------------


def test_a_bank_debit_alert_does_not_flag_the_payees_handle():
    """Found live: a genuine SBI alert quoting "merchant@okhdfcbank" fired the
    personal-payment rule and put a 🚩 on the user's own bank message.

    A completed-transaction notice names the other party as a matter of record.
    The rule is about being *asked* to pay a stranger."""
    alert = msg(
        "Dear Customer, Rs.5,000.00 has been debited from A/c XX1234 on 02-08-26 to VPA "
        "merchant@okhdfcbank (UPI Ref 512345678901). Never share your OTP with anyone. -SBI",
        payment_handles=["merchant@okhdfcbank"],
    )
    assert "personal_payment_handle" not in fired_ids(alert)
    assert evaluate(alert) == []


def test_a_request_to_pay_a_personal_handle_still_fires():
    """The exclusion must not swallow the case the rule exists for."""
    demand = msg(
        "Pay Rs 5,000 to rajesh.k@okaxis to confirm your seat",
        payment_handles=["rajesh.k@okaxis"],
    )
    assert "personal_payment_handle" in fired_ids(demand)


def test_a_scam_that_mentions_a_transaction_and_then_demands_payment_still_fires():
    """"Your account was debited... now pay this to reverse it" is a real pattern."""
    hybrid = msg(
        "Rs 9,999 has been debited from your account. To reverse it immediately, "
        "transfer Rs 500 to suresh.b@ybl",
        payment_handles=["suresh.b@ybl"],
    )
    assert "personal_payment_handle" in fired_ids(hybrid)
