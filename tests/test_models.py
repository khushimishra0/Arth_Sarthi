"""Data layer tests.

Two kinds of assertion here. The ordinary ones — a cache hit is a hit, an upsert
replaces — and a second kind that exists to make the privacy posture in §10 fail
loudly if someone adds a convenient column later. Those are the ones to keep.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import StatementError

from app.models import repo
from app.models.db import (
    ANALYSIS_RETENTION_DAYS,
    Analysis,
    Base,
    Budget,
    Profile,
    User,
    delete_user,
    purge_expired_analyses,
    utcnow,
)
from app.models.enums import (
    INCOME_BAND_MONTHLY_RANGE,
    Channel,
    Feature,
    Gender,
    IncomeBand,
    Language,
    income_band_annual_range,
)

# ---------------------------------------------------------------------------
# The privacy guarantees, as tests
# ---------------------------------------------------------------------------

FORBIDDEN_COLUMN_SUBSTRINGS = (
    "name",
    "phone",
    "mobile",
    "email",
    "address",
    "aadhaar",
    "pan",
    "account",
    "password",
    "otp",
    "file_bytes",
    "content",
    "image",
    "document",
)


def test_no_table_stores_anything_that_identifies_a_person():
    """§10: no name, no phone number, and nowhere to put a document's bytes.

    `input_hash` is the only trace of an upload and it is one-way. If this test
    fails, read the new column name before deleting the assertion.
    """
    offenders = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            # `channel_user_id` is Telegram's own id — the only identifier we hold,
            # and the only way to know who is talking to us at all.
            if column.name == "channel_user_id":
                continue
            if any(bad in column.name.lower() for bad in FORBIDDEN_COLUMN_SUBSTRINGS):
                offenders.append(f"{table.name}.{column.name}")
    assert offenders == []


def test_profile_stores_income_as_a_band_not_a_number():
    columns = {c.name: c for c in inspect(Profile).columns}
    assert "income" not in columns, "a profile must never hold an exact income figure"
    assert set(columns["income_band"].type.enums) == {b.value for b in IncomeBand}


def test_analyses_retention_window_is_ninety_days():
    assert ANALYSIS_RETENTION_DAYS == 90


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def test_first_contact_creates_a_user_with_no_form(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    assert user.id is not None
    assert user.language is Language.ENGLISH
    assert user.created_at.tzinfo is not None


def test_the_same_person_is_never_created_twice(session):
    first = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    second = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id=42)
    assert first.id == second.id
    assert session.query(User).count() == 1


def test_the_same_id_on_two_channels_is_two_people(session):
    telegram = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    web = repo.get_or_create_user(session, channel=Channel.WEB, channel_user_id="42")
    assert telegram.id != web.id


def test_language_choice_persists(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    repo.set_language(session, user, Language.HINDI)
    session.expire_all()
    assert session.get(User, user.id).language is Language.HINDI


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def test_timestamps_come_back_timezone_aware(session):
    """SQLite forgets timezones. The column type must remember for it."""
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    session.expire_all()
    reloaded = session.get(User, user.id)
    assert reloaded.created_at.tzinfo is not None
    assert (utcnow() - reloaded.created_at) < timedelta(seconds=30)


def test_a_naive_datetime_is_refused_rather_than_silently_stored(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    session.add(
        Analysis(
            user_id=user.id,
            feature=Feature.SCAM,
            input_hash="a" * 64,
            result_json={},
            created_at=datetime(2026, 1, 1),  # noqa: DTZ001 - the point of the test
        )
    )
    with pytest.raises(StatementError, match="naive datetime"):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# Analyses and the cache
# ---------------------------------------------------------------------------


def test_an_identical_input_is_served_from_cache(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    repo.record_analysis(
        session, user, feature=Feature.SCAM, input_hash="b" * 64, result={"score": 91}
    )
    hit = repo.cached_analysis(session, feature=Feature.SCAM, input_hash="b" * 64)
    assert hit is not None and hit.result_json == {"score": 91}


def test_the_cache_is_shared_across_users(session):
    """The forwarded screenshot doing the rounds of a family group costs one call."""
    sender = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="1")
    repo.record_analysis(
        session, sender, feature=Feature.SCAM, input_hash="c" * 64, result={"score": 88}
    )
    repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="2")
    assert repo.cached_analysis(session, feature=Feature.SCAM, input_hash="c" * 64) is not None


def test_the_cache_does_not_cross_features(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    repo.record_analysis(session, user, feature=Feature.SCAM, input_hash="d" * 64, result={})
    assert repo.cached_analysis(session, feature=Feature.LOAN, input_hash="d" * 64) is None


def test_a_stale_cache_entry_is_a_miss(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    stored = repo.record_analysis(
        session, user, feature=Feature.SCAM, input_hash="e" * 64, result={}
    )
    stored.created_at = utcnow() - timedelta(days=30)
    session.commit()
    assert repo.cached_analysis(session, feature=Feature.SCAM, input_hash="e" * 64) is None


def test_the_daily_cap_counts_only_today(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    today = repo.record_analysis(
        session, user, feature=Feature.SCAM, input_hash="f" * 64, result={}
    )
    yesterday = repo.record_analysis(
        session, user, feature=Feature.SCAM, input_hash="0" * 64, result={}
    )
    yesterday.created_at = utcnow() - timedelta(days=2)
    session.commit()
    assert today.id is not None
    assert repo.analyses_today(session, user) == 1


def test_retention_purges_old_analyses_and_keeps_recent_ones(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    fresh = repo.record_analysis(
        session, user, feature=Feature.SCAM, input_hash="1" * 64, result={}
    )
    old = repo.record_analysis(session, user, feature=Feature.SCAM, input_hash="2" * 64, result={})
    old.created_at = utcnow() - timedelta(days=ANALYSIS_RETENTION_DAYS + 1)
    session.commit()

    assert purge_expired_analyses(session) == 1
    remaining = session.query(Analysis).all()
    assert [a.id for a in remaining] == [fresh.id]


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_re_entering_a_month_replaces_it_rather_than_appending(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    repo.upsert_budget(session, user, month="2026-08", income=25_000, categories={"rent": 8_000})
    repo.upsert_budget(
        session,
        user,
        month="2026-08",
        income=25_000,
        categories={"rent": 8_500},
        savings_goal=45_000,
        target_date=date(2026, 12, 15),
    )
    budgets = session.query(Budget).all()
    assert len(budgets) == 1
    assert budgets[0].categories_json == {"rent": 8_500}
    assert budgets[0].savings_goal == 45_000
    assert budgets[0].target_date == date(2026, 12, 15)


def test_two_months_are_two_rows(session):
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    repo.upsert_budget(session, user, month="2026-07", income=25_000, categories={})
    repo.upsert_budget(session, user, month="2026-08", income=26_000, categories={})
    assert session.query(Budget).count() == 2


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def test_profile_delete_actually_deletes_everything(session):
    """The promise made on the first screen of /start. It has to be true."""
    user = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="42")
    profile = repo.get_or_create_profile(session, user)
    profile.gender = Gender.FEMALE
    profile.income_band = IncomeBand.FROM_10K_TO_25K
    repo.record_analysis(session, user, feature=Feature.LOAN, input_hash="3" * 64, result={"a": 1})
    repo.upsert_budget(session, user, month="2026-08", income=25_000, categories={"rent": 8_000})
    session.commit()

    assert delete_user(session, channel=Channel.TELEGRAM, channel_user_id="42") is True

    assert session.query(User).count() == 0
    assert session.query(Profile).count() == 0
    assert session.query(Analysis).count() == 0
    assert session.query(Budget).count() == 0


def test_deleting_an_unknown_user_is_not_an_error(session):
    assert delete_user(session, channel=Channel.TELEGRAM, channel_user_id="nobody") is False


def test_one_users_delete_does_not_touch_another(session):
    kept = repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="1")
    repo.record_analysis(session, kept, feature=Feature.SCAM, input_hash="4" * 64, result={})
    repo.get_or_create_user(session, channel=Channel.TELEGRAM, channel_user_id="2")

    delete_user(session, channel=Channel.TELEGRAM, channel_user_id="2")
    assert session.query(User).count() == 1
    assert session.query(Analysis).count() == 1


# ---------------------------------------------------------------------------
# Income bands
# ---------------------------------------------------------------------------


def test_every_income_band_has_bounds():
    assert set(INCOME_BAND_MONTHLY_RANGE) == set(IncomeBand)


def test_the_bands_tile_the_range_without_gaps_or_overlap():
    ordered = [
        IncomeBand.UNDER_10K,
        IncomeBand.FROM_10K_TO_25K,
        IncomeBand.FROM_25K_TO_50K,
        IncomeBand.FROM_50K_TO_1L,
        IncomeBand.OVER_1L,
    ]
    bounds = [INCOME_BAND_MONTHLY_RANGE[b] for b in ordered]
    assert bounds[0][0] == 0
    assert bounds[-1][1] is None
    for (_, high), (low, _) in zip(bounds, bounds[1:], strict=False):
        assert high == low


def test_declining_to_state_income_widens_rather_than_empties():
    """"Prefer not to say" must not disqualify a user from every income-capped scheme."""
    assert INCOME_BAND_MONTHLY_RANGE[IncomeBand.PREFER_NOT_TO_SAY] == (0, None)


def test_the_worked_example_annualises_correctly():
    """§6: ₹12,000/month is stated to the user as ₹1.44 lakh a year."""
    low, high = income_band_annual_range(IncomeBand.FROM_10K_TO_25K)
    assert low == 120_000 and high == 300_000
    assert low <= 12_000 * 12 <= high


def test_utcnow_is_aware():
    assert utcnow().tzinfo is UTC
