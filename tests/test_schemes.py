"""Schemes: eligibility, ranking, the injection fence, and Gate 6's bars.

Gate 6 has three bars and each has a test named after it:

  no ineligible scheme is ever shown
  no scheme is named that is not in the database
  every application URL resolves          (marked `network` — opt in)

The dataset tests are as important as the engine tests. §6's whole argument is that a
model asked "which schemes suit this person" invents ceilings and names discontinued
programmes, so the row is the source of truth — which only holds if every row is
checkable. `test_every_scheme_cites_a_verifiable_source` is what keeps that true.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.channels.base import render_plain
from app.features.schemes.eligibility import eligible_schemes, explain_rejection, is_eligible
from app.features.schemes.explainer import (
    SchemeExplanation,
    explain,
    fallback_explanation,
    verify_explanations,
)
from app.features.schemes.formatter import format_matches, format_no_matches, format_unavailable
from app.features.schemes.ranker import MAX_RESULTS, rank_schemes
from app.features.schemes.schema import (
    Alternative,
    Eligibility,
    Scheme,
    SchemeProfile,
    load_schemes,
)
from app.features.schemes.service import find_schemes, match_profile, profile_summary
from app.llm.mock_driver import MockProvider
from app.models import repo
from app.models.enums import (
    AgeBand,
    Area,
    CasteCategory,
    Channel,
    Gender,
    IncomeBand,
    NeedCategory,
)

DATASET = load_schemes()

# §6's worked example: woman · village · ₹12,000/month · education
WORKED_PROFILE = SchemeProfile(
    gender=Gender.FEMALE,
    age_band=AgeBand.AGE_18_25,
    state="bihar",
    area=Area.RURAL,
    income_band=IncomeBand.FROM_10K_TO_25K,
    caste_category=CasteCategory.PREFER_NOT_TO_SAY,
    needs=(NeedCategory.EDUCATION,),
)


def fixture_scheme(**overrides) -> Scheme:
    """A minimal valid row, for testing filters without leaning on real data."""
    defaults = dict(
        id="test_scheme",
        name="Test Scheme",
        level="central",
        category="savings",
        benefit_type="savings",
        benefit_text="A test benefit of ₹1,000.",
        source_url="https://example.gov.in/scheme",
        last_verified=date(2026, 8, 1),
    )
    return Scheme.model_validate({**defaults, **overrides})


# ---------------------------------------------------------------------------
# The dataset itself
# ---------------------------------------------------------------------------


def test_the_dataset_loads_and_validates():
    assert DATASET, "data/schemes.json is empty — the feature cannot run"


def test_every_scheme_cites_a_verifiable_source():
    """§6: the row is the source of truth, which only holds if it can be checked."""
    for scheme in DATASET:
        assert scheme.source_url.startswith("https://"), scheme.id
        assert scheme.last_verified <= date.today() + timedelta(days=1), scheme.id


def test_every_scheme_says_what_it_gives_and_how_to_get_it():
    for scheme in DATASET:
        assert len(scheme.benefit_text) > 40, f"{scheme.id} benefit_text is too thin"
        assert scheme.how_to_apply, f"{scheme.id} has no application route"
        assert scheme.application_url.startswith("https://"), scheme.id


def test_scheme_ids_are_unique():
    ids = [s.id for s in DATASET]
    assert len(ids) == len(set(ids))


def test_the_dataset_covers_every_need_the_bot_offers():
    """A need with no scheme behind it is a dead end after six taps."""
    covered = {s.category for s in DATASET} | {
        purpose for s in DATASET for purpose in s.eligibility.purpose
    }
    missing = {need.value for need in NeedCategory} - covered
    assert missing == set(), f"needs with no scheme: {missing}"


def test_small_savings_schemes_do_not_quote_a_rate_that_expires():
    """Rates are revised quarterly; a number written here is wrong within months.

    Sukanya and MSSC are the deliberate exceptions — their notified rates were
    confirmed at verification time and are flagged in the file as first to re-check.
    """
    rate_free = {"public_provident_fund", "senior_citizen_savings"}
    for scheme in DATASET:
        if scheme.id in rate_free:
            assert "%" not in scheme.benefit_text, (
                f"{scheme.id} quotes a rate that will be stale in a quarter"
            )


# ---------------------------------------------------------------------------
# Stage 1 — hard filters
# ---------------------------------------------------------------------------


def test_a_womens_scheme_is_not_shown_to_a_man():
    scheme = fixture_scheme(eligibility=Eligibility(gender="female"))
    assert not is_eligible(scheme, SchemeProfile(gender=Gender.MALE))
    assert is_eligible(scheme, SchemeProfile(gender=Gender.FEMALE))


def test_an_age_limited_scheme_respects_the_band():
    scheme = fixture_scheme(eligibility=Eligibility(age_min=18, age_max=40))
    assert is_eligible(scheme, SchemeProfile(age_band=AgeBand.AGE_26_35))
    assert not is_eligible(scheme, SchemeProfile(age_band=AgeBand.OVER_60))
    assert not is_eligible(scheme, SchemeProfile(age_band=AgeBand.UNDER_18))


def test_an_age_band_overlapping_the_limit_is_kept():
    """A 26-35 applicant against a scheme for up to 30 has eligible years."""
    scheme = fixture_scheme(eligibility=Eligibility(age_min=18, age_max=30))
    assert is_eligible(scheme, SchemeProfile(age_band=AgeBand.AGE_26_35))


def test_a_state_scheme_is_not_shown_outside_that_state():
    scheme = fixture_scheme(level="state", states=["bihar"])
    assert is_eligible(scheme, SchemeProfile(state="bihar"))
    assert not is_eligible(scheme, SchemeProfile(state="kerala"))


def test_a_rural_scheme_is_not_shown_to_a_city_applicant():
    scheme = fixture_scheme(eligibility=Eligibility(area="rural"))
    assert is_eligible(scheme, SchemeProfile(area=Area.RURAL))
    assert not is_eligible(scheme, SchemeProfile(area=Area.URBAN))


def test_an_income_ceiling_drops_only_bands_entirely_above_it():
    """Income is a band. Someone in an overlapping band may still qualify, and
    hiding the scheme would cost them a benefit they are entitled to."""
    scheme = fixture_scheme(eligibility=Eligibility(income_max_annual=300_000))

    assert is_eligible(scheme, SchemeProfile(income_band=IncomeBand.UNDER_10K))
    assert is_eligible(scheme, SchemeProfile(income_band=IncomeBand.FROM_10K_TO_25K))
    # 25k-50k/month is 3-6 lakh/year — its floor is at the ceiling, so kept.
    assert is_eligible(scheme, SchemeProfile(income_band=IncomeBand.FROM_25K_TO_50K))
    # Over 1 lakh/month starts at 12 lakh/year — nobody in it qualifies.
    assert not is_eligible(scheme, SchemeProfile(income_band=IncomeBand.OVER_1L))


def test_a_reserved_scheme_respects_the_category():
    scheme = fixture_scheme(eligibility=Eligibility(caste_category=["SC", "ST"]))
    assert is_eligible(scheme, SchemeProfile(caste_category=CasteCategory.SC))
    assert not is_eligible(scheme, SchemeProfile(caste_category=CasteCategory.GENERAL))


# ---------------------------------------------------------------------------
# "Prefer not to say" must widen, never narrow — §6
# ---------------------------------------------------------------------------


def test_an_unanswered_question_never_disqualifies_anything():
    narrow = fixture_scheme(
        level="state",
        states=["bihar"],
        eligibility=Eligibility(
            gender="female", age_min=18, age_max=40, area="rural",
            income_max_annual=100_000, caste_category=["SC"],
        ),
    )
    assert is_eligible(narrow, SchemeProfile())  # nothing answered at all


def test_declining_a_question_widens_results():
    """The enum member exists so the user can choose it; the matcher must not see it."""
    declined = SchemeProfile.from_row(
        type("Row", (), {
            "gender": Gender.PREFER_NOT_TO_SAY,
            "age_band": AgeBand.PREFER_NOT_TO_SAY,
            "state": "prefer_not_to_say",
            "area": Area.PREFER_NOT_TO_SAY,
            "income_band": IncomeBand.PREFER_NOT_TO_SAY,
            "category": CasteCategory.PREFER_NOT_TO_SAY,
            "needs": ["education"],
        })()
    )
    assert declined.gender is None
    assert declined.state is None
    assert declined.caste_category is None
    assert declined.needs == (NeedCategory.EDUCATION,)

    stated = SchemeProfile(gender=Gender.MALE, caste_category=CasteCategory.GENERAL)
    declined_matches, _ = eligible_schemes(DATASET, declined)
    stated_matches, _ = eligible_schemes(DATASET, stated)
    assert len(declined_matches) >= len(stated_matches)


# ---------------------------------------------------------------------------
# Schemes with more than one way to qualify
# ---------------------------------------------------------------------------


def test_a_scheme_open_to_sc_st_or_any_woman_handles_all_four_cases():
    """Stand-Up India. Flat fields cannot express this without a false result either
    way — excluding a General woman, or including a General man."""
    scheme = fixture_scheme(
        eligibility=Eligibility(
            any_of=[Alternative(caste_category=["SC", "ST"]), Alternative(gender="female")]
        )
    )
    cases = [
        (Gender.FEMALE, CasteCategory.GENERAL, True),
        (Gender.MALE, CasteCategory.SC, True),
        (Gender.FEMALE, CasteCategory.ST, True),
        (Gender.MALE, CasteCategory.GENERAL, False),
        (Gender.MALE, CasteCategory.OBC, False),
    ]
    for gender, caste, expected in cases:
        profile = SchemeProfile(gender=gender, caste_category=caste)
        assert is_eligible(scheme, profile) is expected, (gender, caste)


def test_an_unanswered_question_satisfies_an_alternative():
    scheme = fixture_scheme(
        eligibility=Eligibility(any_of=[Alternative(caste_category=["SC", "ST"])])
    )
    assert is_eligible(scheme, SchemeProfile(gender=Gender.MALE))  # caste unstated


def test_the_real_stand_up_india_row_uses_alternatives():
    scheme = next(s for s in DATASET if s.id == "stand_up_india")
    assert scheme.eligibility.any_of, "Stand-Up India needs any_of or it filters wrongly"
    general_woman = SchemeProfile(gender=Gender.FEMALE, caste_category=CasteCategory.GENERAL)
    general_man = SchemeProfile(gender=Gender.MALE, caste_category=CasteCategory.GENERAL)
    assert is_eligible(scheme, general_woman)
    assert not is_eligible(scheme, general_man)


# ---------------------------------------------------------------------------
# Stage 2 — ranking
# ---------------------------------------------------------------------------


def test_at_most_five_schemes_are_returned():
    """§6 asks for the top 5."""
    ranked, _ = match_profile(SchemeProfile(needs=(NeedCategory.SAVINGS,)), DATASET)
    assert len(ranked) <= MAX_RESULTS


def test_what_the_user_asked_for_ranks_first():
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    assert ranked[0].scheme.category == "education" or "education" in (
        ranked[0].scheme.eligibility.purpose
    )
    assert ranked[0].is_best_match


def test_the_worked_example_puts_vidyalakshmi_first():
    """§6's own example labels PM Vidyalakshmi as the best match."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    assert ranked[0].scheme.id == "pm_vidyalakshmi"


def test_purpose_outweighs_ease():
    """A relevant scheme with a hard form beats an easy irrelevant one — ×3 vs ×1."""
    relevant_hard = fixture_scheme(
        id="relevant", category="housing", application_difficulty=5
    )
    irrelevant_easy = fixture_scheme(
        id="irrelevant", category="savings", application_difficulty=1
    )
    ranked = rank_schemes(
        (irrelevant_easy, relevant_hard), SchemeProfile(needs=(NeedCategory.HOUSING,))
    )
    assert ranked[0].scheme.id == "relevant"


def test_a_benefit_is_scored_against_this_persons_income():
    """₹6,000 matters at ₹1.44 lakh a year and is noise at ₹18 lakh."""
    scheme = fixture_scheme(benefit_amount=6_000, category="savings")
    low = rank_schemes((scheme,), SchemeProfile(
        income_band=IncomeBand.UNDER_10K, needs=(NeedCategory.SAVINGS,)))
    high = rank_schemes((scheme,), SchemeProfile(
        income_band=IncomeBand.OVER_1L, needs=(NeedCategory.SAVINGS,)))
    assert low[0].benefit > high[0].benefit


def test_ranking_is_stable_across_runs():
    """A demo that reorders between two runs looks broken."""
    first, _ = match_profile(WORKED_PROFILE, DATASET)
    second, _ = match_profile(WORKED_PROFILE, DATASET)
    assert [r.scheme.id for r in first] == [r.scheme.id for r in second]


def test_an_unanswered_income_does_not_push_schemes_down():
    scheme = fixture_scheme(benefit_amount=50_000)
    without = rank_schemes((scheme,), SchemeProfile())
    assert without[0].benefit == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Gate 6, bar 1 — no ineligible scheme is ever shown
# ---------------------------------------------------------------------------

TEN_PROFILES = [
    pytest.param(WORKED_PROFILE, id="woman_village_12k_education"),
    pytest.param(
        SchemeProfile(gender=Gender.MALE, age_band=AgeBand.AGE_36_45, state="uttar_pradesh",
                      area=Area.RURAL, income_band=IncomeBand.UNDER_10K,
                      caste_category=CasteCategory.SC, needs=(NeedCategory.FARMING,)),
        id="sc_farmer_up",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.FEMALE, age_band=AgeBand.AGE_26_35, state="maharashtra",
                      area=Area.URBAN, income_band=IncomeBand.FROM_10K_TO_25K,
                      caste_category=CasteCategory.OBC, needs=(NeedCategory.BUSINESS,)),
        id="woman_city_business",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.MALE, age_band=AgeBand.OVER_60, state="kerala",
                      area=Area.URBAN, income_band=IncomeBand.FROM_25K_TO_50K,
                      caste_category=CasteCategory.GENERAL, needs=(NeedCategory.PENSION,)),
        id="senior_citizen_pension",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.FEMALE, age_band=AgeBand.UNDER_18, state="bihar",
                      area=Area.RURAL, income_band=IncomeBand.UNDER_10K,
                      caste_category=CasteCategory.ST, needs=(NeedCategory.EDUCATION,)),
        id="st_girl_child_education",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.MALE, age_band=AgeBand.AGE_18_25, state="delhi",
                      area=Area.URBAN, income_band=IncomeBand.FROM_10K_TO_25K,
                      caste_category=CasteCategory.GENERAL, needs=(NeedCategory.HOUSING,)),
        id="young_man_city_housing",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.FEMALE, age_band=AgeBand.AGE_46_60, state="west_bengal",
                      area=Area.RURAL, income_band=IncomeBand.UNDER_10K,
                      caste_category=CasteCategory.PREFER_NOT_TO_SAY,
                      needs=(NeedCategory.HEALTH,)),
        id="rural_woman_health",
    ),
    pytest.param(
        SchemeProfile(gender=Gender.OTHER, age_band=AgeBand.AGE_26_35, state="tamil_nadu",
                      area=Area.URBAN, income_band=IncomeBand.FROM_25K_TO_50K,
                      caste_category=CasteCategory.GENERAL, needs=(NeedCategory.SAVINGS,)),
        id="other_gender_savings",
    ),
    pytest.param(
        SchemeProfile(income_band=IncomeBand.OVER_1L, needs=(NeedCategory.SAVINGS,)),
        id="high_income_minimal_profile",
    ),
    pytest.param(SchemeProfile(needs=(NeedCategory.BUSINESS,)), id="nothing_stated_but_need"),
]


@pytest.mark.parametrize("profile", TEN_PROFILES)
def test_no_ineligible_scheme_is_ever_shown(profile):
    """Gate 6, bar 1. Being sent to a counter and turned away is the failure."""
    ranked, _ = match_profile(profile, DATASET)
    for item in ranked:
        reason = explain_rejection(item.scheme, profile)
        assert reason is None, f"{item.scheme.id} was shown but: {reason}"


@pytest.mark.parametrize("profile", TEN_PROFILES)
def test_every_profile_gets_a_usable_answer(profile):
    """Either matches, or an honest "my database is small" — never a dead end."""
    ranked, _ = match_profile(profile, DATASET)
    message = (
        format_matches(ranked, profile, {}) if ranked else format_no_matches(profile)
    )
    assert render_plain(message).strip()
    assert message.keyboard is not None


# ---------------------------------------------------------------------------
# Gate 6, bar 2 — no scheme is named that is not in the database
# ---------------------------------------------------------------------------


def test_an_explanation_for_a_scheme_we_did_not_supply_is_dropped():
    """The model cannot name what it was not given, but verify anyway."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    invented = [SchemeExplanation(scheme_id="totally_made_up", explanation="Great scheme.")]
    assert verify_explanations(invented, ranked) == {}


def test_an_explanation_quoting_a_figure_not_in_the_record_is_dropped():
    """§6: "do not state any figure not present in the record"."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    target = ranked[0].scheme.id
    hallucinated = [
        SchemeExplanation(
            scheme_id=target,
            explanation="You can get up to ₹99,99,999 under this scheme.",
        )
    ]
    assert verify_explanations(hallucinated, ranked) == {}


def test_an_explanation_using_only_the_records_figures_is_kept():
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    scheme = ranked[0].scheme
    faithful = [
        SchemeExplanation(
            scheme_id=scheme.id,
            explanation="This gives an education loan of up to ₹7.5 lakh with no security.",
        )
    ]
    accepted = verify_explanations(faithful, ranked)
    assert scheme.id in accepted


def test_an_explanation_with_no_figures_at_all_is_kept():
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    scheme = ranked[0].scheme
    prose = [
        SchemeExplanation(
            scheme_id=scheme.id,
            explanation="This helps you pay for college without putting up security.",
        )
    ]
    assert scheme.id in verify_explanations(prose, ranked)


def test_with_no_provider_every_scheme_falls_back_to_its_own_words():
    """No key, no cost, still truthful — which is why the suite needs no API key."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    explanations = explain(ranked, profile_summary(WORKED_PROFILE), None)
    for item in ranked:
        assert explanations[item.scheme.id] == fallback_explanation(item.scheme)


def test_only_matched_rows_are_sent_to_the_model():
    """The structural half of the defence: it cannot name what it never saw."""
    provider = MockProvider()
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    explain(ranked, profile_summary(WORKED_PROFILE), provider)

    call = provider.calls_to("analyse")[0]
    sent = {s["id"] for s in call.context["schemes"]}
    assert sent == {item.scheme.id for item in ranked}
    assert len(sent) <= MAX_RESULTS


def test_the_prompt_forbids_naming_anything_else():
    from app.features.schemes.explainer import EXPLAIN_PROMPT

    assert "ONLY the schemes listed below" in EXPLAIN_PROMPT
    assert "not present in that scheme's own record" in EXPLAIN_PROMPT


def test_the_model_is_told_nothing_that_identifies_the_person():
    """§10 holds here too: bands, not figures; no name, no phone."""
    summary = profile_summary(WORKED_PROFILE)
    assert set(summary) == {
        "gender", "age_band", "state", "area", "income_band", "caste_category", "needs"
    }
    assert summary["income_band"] == "10k_25k"  # a band, never a rupee figure


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


def test_every_card_shows_its_verification_date():
    """§6: the demo displays the verification date, so a stale row is visible."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    text = render_plain(format_matches(ranked, WORKED_PROFILE, {}))
    assert "Details checked" in text
    for item in ranked:
        assert item.scheme.last_verified.strftime("%d %B %Y") in text


def test_the_response_summarises_what_it_matched_on():
    text = render_plain(format_matches(*_matched()))
    assert "Based on:" in text
    assert "woman" in text and "village" in text and "education" in text


def test_only_the_first_card_can_be_labelled_best_match():
    text = render_plain(format_matches(*_matched()))
    assert text.count("Best match") <= 1


def test_conditions_the_engine_cannot_check_are_shown_to_the_user():
    """We cannot verify "admission to a recognised institution" — so we say it."""
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    text = render_plain(format_matches(ranked, WORKED_PROFILE, {}))
    with_conditions = [i for i in ranked if i.scheme.eligibility.conditions]
    assert with_conditions
    assert with_conditions[0].scheme.eligibility.conditions[0] in text


def test_no_matches_says_the_database_is_small_not_that_you_qualify_for_nothing():
    """Those are very different statements and only one of them is true."""
    text = render_plain(format_no_matches(SchemeProfile()))
    assert "does not mean nothing exists for you" in text
    assert "still small" in text


def test_a_missing_dataset_refuses_to_guess():
    text = render_plain(format_unavailable())
    assert "will not guess" in text
    assert "problem on my side" in text


def test_the_response_carries_its_disclaimer():
    text = render_plain(format_matches(*_matched()))
    assert "Confirm current terms on the official portal" in text


def test_the_feature_never_emits_channel_markup():
    for message in (
        format_matches(*_matched()),
        format_no_matches(SchemeProfile()),
        format_unavailable(),
    ):
        text = render_plain(message)
        assert "<" not in text and "**" not in text


def _matched():
    ranked, _ = match_profile(WORKED_PROFILE, DATASET)
    return ranked, WORKED_PROFILE, {}


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


async def test_the_worked_example_end_to_end(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="66")
    response = await find_schemes(
        WORKED_PROFILE, session=session, user=user, provider=MockProvider()
    )
    text = render_plain(response)
    assert "You may qualify for" in text
    assert "PM Vidyalaxmi" in text


async def test_the_match_is_recorded_without_personal_detail(session):
    from app.models.db import Analysis
    from app.models.enums import Feature

    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="66")
    await find_schemes(WORKED_PROFILE, session=session, user=user, provider=None)

    row = session.query(Analysis).one()
    assert row.feature is Feature.SCHEMES
    stored = str(row.result_json)
    assert "pm_vidyalakshmi" in stored
    assert "bihar" not in stored  # only ids and scores, not the profile


async def test_matching_works_with_no_provider_at_all(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="66")
    response = await find_schemes(
        WORKED_PROFILE, session=session, user=user, provider=None
    )
    assert "PM Vidyalaxmi" in render_plain(response)


# ---------------------------------------------------------------------------
# Gate 6, bar 3 — every application URL resolves.
# Opt in with: python -m pytest -m network
# ---------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.parametrize("scheme", DATASET, ids=[s.id for s in DATASET])
def test_every_application_url_resolves(scheme):
    """Gate 6, bar 3. A dead apply link sends someone nowhere.

    Two outcomes are deliberately distinguished, because conflating them makes this
    test useless:

    **An HTTP error is a dataset bug** — the server answered and said the page is
    gone. That fails, and the row needs fixing.

    **A transport error is a network fact** — DNS, TLS or a timeout. Several Indian
    government portals have incomplete certificate chains or refuse to resolve from
    outside India; `nha.gov.in` and `pmayg.nic.in` both do at the time of writing,
    while `pmkisan.gov.in` and `pib.gov.in` answer fine from the same machine. That
    is reported as a skip, not a failure, or this test would be red on every machine
    for reasons the dataset cannot fix.

    ⚠ It is still worth knowing before a demo: a link that will not open from the
    venue's wifi is a link that fails on stage. Run this from the demo network.
    """
    import httpx

    for url in sorted({scheme.application_url, scheme.source_url}):
        if not url:
            continue
        try:
            response = httpx.head(url, timeout=20.0, follow_redirects=True)
            if response.status_code >= 400:
                response = httpx.get(url, timeout=20.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            pytest.skip(f"{url} not reachable from this network — {type(exc).__name__}")
        assert response.status_code < 400, (
            f"{scheme.id}: {url} returned {response.status_code} — the row needs updating"
        )
