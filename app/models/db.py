"""SQLAlchemy tables — the four from §10 of the plan, and nothing more.

What is deliberately absent is the point of this file. There is no `name` column,
no `phone_number`, no `email`, and no column that could hold the bytes of an
uploaded document. A loan sanction letter contains a name, an address, an account
number and sometimes an Aadhaar number; a WhatsApp screenshot contains a phone
number and somebody's private conversation. Neither is ever written to disk. What
survives an analysis is a content hash — enough to serve the same forwarded
screenshot from cache, useless to anyone who steals the file.

Retention:
  users, profiles, budgets  kept until the user deletes them (`/profile` delete
                            actually deletes — see `delete_user`)
  analyses                  90 days, then purged by `purge_expired_analyses`
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as SASession

from app.models.enums import (
    AgeBand,
    Area,
    CasteCategory,
    Channel,
    Feature,
    Gender,
    IncomeBand,
    Language,
)

__all__ = [
    "ANALYSIS_RETENTION_DAYS",
    "Analysis",
    "Base",
    "Budget",
    "LlmCall",
    "Profile",
    "User",
    "delete_user",
    "purge_expired_analyses",
    "utcnow",
]

ANALYSIS_RETENTION_DAYS = 90


def utcnow() -> datetime:
    """Timezone-aware UTC. Naive datetimes in a database are a bug waiting to be shipped."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """A timestamp that is UTC-aware on the way in *and* on the way out.

    SQLite stores no timezone, so a value written as aware comes back naive and the
    next comparison against `utcnow()` raises "can't compare offset-naive and
    offset-aware datetimes" — in a feature, at runtime, not in this file. Normalising
    at the column boundary means the rest of the codebase never has to know.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("refusing to store a naive datetime; use utcnow()")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _enum_column(enum_type: type, **kwargs: Any) -> Mapped[Any]:
    """Store enums as their string values, not as a native DB enum type.

    SQLite has no ENUM, and a native type would make adding a scheme category in
    Phase 6 a migration instead of an edit.
    """
    return mapped_column(
        SAEnum(enum_type, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        **kwargs,
    )


class Base(DeclarativeBase):
    pass


class User(Base):
    """One row per person per channel. Identified only by the channel's own id."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("channel", "channel_user_id", name="uq_users_channel_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[Channel] = _enum_column(Channel, nullable=False)
    channel_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[Language] = _enum_column(Language, nullable=False, default=Language.ENGLISH)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    profile: Mapped[Profile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    budgets: Mapped[list[Budget]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} channel={self.channel}>"


class Profile(Base):
    """Answers to the six scheme questions. Every field is optional and editable.

    `needs` is a JSON list of NeedCategory values — a user can want help with both
    education and housing, and forcing a single choice would hide half their matches.
    """

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    gender: Mapped[Gender | None] = _enum_column(Gender, nullable=True)
    age_band: Mapped[AgeBand | None] = _enum_column(AgeBand, nullable=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area: Mapped[Area | None] = _enum_column(Area, nullable=True)
    income_band: Mapped[IncomeBand | None] = _enum_column(IncomeBand, nullable=True)
    category: Mapped[CasteCategory | None] = _enum_column(CasteCategory, nullable=True)
    needs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Analysis(Base):
    """A completed run of one engine.

    `input_hash` is a SHA-256 of the input bytes or text — the cache key that stops
    the same forwarded screenshot costing a second vision call. The input itself is
    not here. `result_json` holds the structured output, which is what the formatter
    re-renders on "show me that again" without paying for the analysis twice.
    """

    __tablename__ = "analyses"
    __table_args__ = (
        Index("ix_analyses_cache_lookup", "feature", "input_hash"),
        Index("ix_analyses_user_recent", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[Feature] = _enum_column(Feature, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="analyses")


class Budget(Base):
    """One month of a user's own numbers, so month-on-month tracking works.

    Exact rupee figures live here rather than bands — see the note in enums.py.
    `month` is "YYYY-MM"; one budget per user per month, overwritten on re-entry.
    """

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_budgets_user_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    income: Mapped[float] = mapped_column(Float, nullable=False)
    categories_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False, default=dict)
    savings_goal: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_date: Mapped[date | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="budgets")


class LlmCall(Base):
    """One billable call to a provider. The monthly spend cap is a SUM over this.

    Deliberately **not** linked to a user. Two reasons, and the second is the real
    one: spend is a system-level fact, and a `user_id` here would either be deleted
    along with the user — silently resetting the cap and letting the bill run past
    it — or would survive the delete and break the promise on `/start`. Neither is
    acceptable, so the row simply does not know who caused it.

    `cost_inr` is an estimate from published per-token pricing. Nobody is billed off
    it; it exists to decide when to stop spending.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    feature: Mapped[Feature | None] = _enum_column(Feature, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


def purge_expired_analyses(session: SASession, *, now: datetime | None = None) -> int:
    """Delete analyses older than the retention window. Returns the row count.

    Called on startup rather than on a schedule: a prototype that is restarted more
    often than it is left running gets the same effect with none of the machinery.
    """
    cutoff = (now or utcnow()) - timedelta(days=ANALYSIS_RETENTION_DAYS)
    result = session.execute(delete(Analysis).where(Analysis.created_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)


def delete_user(session: SASession, *, channel: Channel, channel_user_id: str) -> bool:
    """`/profile` delete. Removes the user and every row that hangs off them.

    Returns False if there was nothing to delete. The ORM cascade does the work, so
    a table added later is covered by declaring its relationship, not by editing this.
    """
    user = session.scalar(
        select(User).where(User.channel == channel, User.channel_user_id == channel_user_id)
    )
    if user is None:
        return False
    session.delete(user)
    session.commit()
    return True
