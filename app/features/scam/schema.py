"""What the vision model is allowed to return.

`ExtractedMessage` is §4 verbatim, and the constraint on it is the whole design:
**the extraction step transcribes, it does not judge.** There is no `is_scam` field,
no `risk` field, nowhere for the model to put an opinion. It reads the screenshot
and writes down what is there. Judging happens afterwards, in `rules.py` and
`scorer.py`, in code that can be read, tested, and shown to a user.

`ScamJudgement` is where the model *is* asked for an opinion — deliberately a
separate call, worth 40% of the score, and never able to move a result on its own.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ExtractedMessage", "ScamJudgement"]


class ExtractedMessage(BaseModel):
    """A transcription of what the screenshot contains. No judgement anywhere."""

    message_text: str = Field(description="Verbatim message text, original script preserved")
    language: str = Field(default="en", description="en | hi | hinglish | other")
    sender_name: str | None = Field(default=None, description="Display name or number shown")
    sender_is_group: bool = Field(default=False, description="Was this sent in a group chat")
    links: list[str] = Field(default_factory=list, description="Every URL visible")
    claimed_entity: str | None = Field(
        default=None, description='Organisation claimed, e.g. "SEBI registered advisor"'
    )
    claimed_return_pct: float | None = Field(
        default=None, description="Return percentage promised, as a number"
    )
    claimed_period_days: int | None = Field(
        default=None, description="Period the return is promised over, in days"
    )
    payment_handles: list[str] = Field(
        default_factory=list, description="UPI IDs, account numbers, wallet handles visible"
    )
    registration_number: str | None = Field(
        default=None, description="SEBI/RBI/IRDAI registration number if actually shown"
    )


class ScamJudgement(BaseModel):
    """The model's independent opinion — step 4 of §4.

    Asked separately from extraction so it cannot quietly bias the transcription,
    and capped at 40% of the blend so a confident wrong answer cannot carry a result.
    """

    score: int = Field(ge=0, le=100, description="0 = certainly genuine, 100 = certainly a scam")
    reasoning: str = Field(default="", description="One or two sentences, for the log not the user")
