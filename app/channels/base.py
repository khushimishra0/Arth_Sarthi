"""The channel-neutral message contract.

Everything above this line is a transport — Telegram today, the web dashboard in
Phase 7, WhatsApp when someone pays for the Business API. Everything below it is a
feature. A feature returns an `OutboundMessage`: blocks of content, images, and an
optional keyboard. It never returns Telegram markup, never escapes MarkdownV2,
never knows what a chat_id is.

That is what makes the WhatsApp port one file rather than a rewrite. It is also
what makes the features testable — a scam formatter's output can be asserted
against structured blocks instead of against a wall of escaped asterisks.

Inbound runs the same way in reverse. A channel hands over
`{user_id, intent, files[], text}` and `classify()` — pure, no Telegram import —
decides what the user meant. §8's zero-command routing is tested here, not in a
handler that needs a bot token to exercise.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.enums import Channel, Language
from app.utils.hashing import content_hash

__all__ = [
    "Attachment",
    "AttachmentKind",
    "Block",
    "BlockKind",
    "Button",
    "ChannelAdapter",
    "InboundMessage",
    "Keyboard",
    "OutboundMessage",
    "OutboundImage",
    "Intent",
    "classify",
    "looks_like_budget_text",
    "render_plain",
]


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


class Intent(StrEnum):
    """What the user wants. Derived from a command, or inferred from what arrived."""

    START = "start"
    HELP = "help"
    SCAM_CHECK = "scam_check"
    LOAN_ANALYSIS = "loan_analysis"
    SCHEMES = "schemes"
    BUDGET = "budget"
    PROFILE = "profile"
    LANGUAGE = "language"
    UNKNOWN = "unknown"


class AttachmentKind(StrEnum):
    IMAGE = "image"
    PDF = "pdf"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Attachment:
    """An uploaded file, held in memory and never written to disk.

    `content` is excluded from the repr so a stray log line or an exception
    traceback cannot spill somebody's sanction letter into a log file.
    """

    kind: AttachmentKind
    content: bytes = field(repr=False)
    filename: str | None = None
    mime_type: str | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    @property
    def digest(self) -> str:
        """Cache key and the only part of this file that is ever persisted."""
        return content_hash(self.content)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A normalised inbound event. What every channel produces, whatever it speaks."""

    user_id: str
    channel: Channel
    intent: Intent
    text: str = ""
    files: tuple[Attachment, ...] = ()
    forwarded: bool = False
    language: Language = Language.ENGLISH

    @property
    def has_image(self) -> bool:
        return any(f.kind is AttachmentKind.IMAGE for f in self.files)

    @property
    def has_pdf(self) -> bool:
        return any(f.kind is AttachmentKind.PDF for f in self.files)


# Words that, next to a number, mean somebody is describing their month.
# English, Hindi and the Hinglish people actually type. Phase 5 owns the real
# parser; this only has to be confident enough to *offer* the budget coach.
_BUDGET_WORDS = (
    "income",
    "salary",
    "kamai",
    "आय",
    "वेतन",
    "rent",
    "kiraya",
    "किराया",
    "food",
    "khana",
    "ration",
    "खाना",
    "travel",
    "transport",
    "petrol",
    "phone",
    "recharge",
    "data",
    "medical",
    "dawai",
    "school",
    "fees",
    "education",
    "emi",
    "loan",
    "kharcha",
    "expense",
    "expenses",
    "budget",
    "saving",
    "savings",
    "bachat",
)

# 8000, 8,000, 8k, ₹8000 — "8k" is how the number is usually typed.
_NUMBER = re.compile(r"(?:₹\s*)?\d[\d,]*(?:\.\d+)?\s*(?:k|K|हज़ार)?\b")


def looks_like_budget_text(text: str) -> bool:
    """True when a message reads like income/expense figures rather than prose.

    Two independent signals are required — a budget word *and* a number — because
    "my loan was rejected" is not a budget and must not be answered with a pie chart.
    """
    if not text:
        return False
    lowered = text.lower()
    if not any(word in lowered for word in _BUDGET_WORDS):
        return False
    return bool(_NUMBER.search(text))


def classify(
    *,
    command: str | None = None,
    text: str = "",
    files: tuple[Attachment, ...] = (),
    forwarded: bool = False,
) -> Intent:
    """§8's zero-command routing, as a pure function.

    Priority, and why:

    1. An explicit command. The user said what they wanted.
    2. An attachment. A PDF is a loan document and a photo is a screenshot —
       this is more specific than the forwarded flag, so a forwarded PDF goes to
       the loan analyser rather than to scam detection.
    3. The forwarded flag. Forwarding a message is exactly the behaviour scam
       detection exists to interrupt.
    4. Text that reads like a month's figures.
    5. UNKNOWN — which the channel answers with the four buttons. Never with
       "invalid command".
    """
    if command:
        try:
            return Intent(command.lstrip("/").split("@")[0].lower())
        except ValueError:
            return Intent.UNKNOWN

    if any(f.kind is AttachmentKind.PDF for f in files):
        return Intent.LOAN_ANALYSIS
    if any(f.kind is AttachmentKind.IMAGE for f in files):
        return Intent.SCAM_CHECK
    if forwarded:
        return Intent.SCAM_CHECK
    if looks_like_budget_text(text):
        return Intent.BUDGET
    return Intent.UNKNOWN


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


class BlockKind(StrEnum):
    HEADING = "heading"
    TEXT = "text"
    BULLETS = "bullets"
    STEPS = "steps"
    KEY_VALUE = "key_value"
    DIVIDER = "divider"
    DISCLAIMER = "disclaimer"


@dataclass(frozen=True, slots=True)
class Block:
    """One unit of content. Constructed through the classmethods, not directly."""

    kind: BlockKind
    text: str = ""
    items: tuple[str, ...] = ()
    pairs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def heading(cls, text: str) -> Block:
        return cls(BlockKind.HEADING, text=text)

    @classmethod
    def para(cls, text: str) -> Block:
        return cls(BlockKind.TEXT, text=text)

    @classmethod
    def bullets(cls, items: list[str] | tuple[str, ...]) -> Block:
        return cls(BlockKind.BULLETS, items=tuple(items))

    @classmethod
    def steps(cls, items: list[str] | tuple[str, ...]) -> Block:
        """An *ordered* list. Numbering is the renderer's job, not the caller's —
        "what to do now" is a sequence, and a caller that types its own "1." ends up
        with "• 1." the moment a channel adds a bullet."""
        return cls(BlockKind.STEPS, items=tuple(items))

    @classmethod
    def key_values(cls, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> Block:
        """Label/value rows. The value goes on its own line — §8's design rule for
        the segments in slide 9: the key number is never buried in a sentence."""
        return cls(BlockKind.KEY_VALUE, pairs=tuple(pairs))

    @classmethod
    def divider(cls) -> Block:
        return cls(BlockKind.DIVIDER)

    @classmethod
    def disclaimer(cls, text: str) -> Block:
        return cls(BlockKind.DISCLAIMER, text=text)


@dataclass(frozen=True, slots=True)
class OutboundImage:
    """A rendered PNG — a budget pie chart, a loan cost comparison."""

    png: bytes = field(repr=False)
    caption: str = ""
    filename: str = "chart.png"


@dataclass(frozen=True, slots=True)
class Button:
    """`action` is an internal callback token; `url` opens a link instead."""

    label: str
    action: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if bool(self.action) == bool(self.url):
            raise ValueError(f"button {self.label!r} needs exactly one of action or url")


@dataclass(frozen=True, slots=True)
class Keyboard:
    rows: tuple[tuple[Button, ...], ...] = ()

    @classmethod
    def of(cls, *rows: list[Button] | tuple[Button, ...]) -> Keyboard:
        return cls(tuple(tuple(row) for row in rows))

    @property
    def buttons(self) -> tuple[Button, ...]:
        return tuple(b for row in self.rows for b in row)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What every feature returns. `{blocks[], images[], keyboard}` and nothing else."""

    blocks: tuple[Block, ...] = ()
    images: tuple[OutboundImage, ...] = ()
    keyboard: Keyboard | None = None

    @classmethod
    def of(
        cls,
        *blocks: Block,
        images: tuple[OutboundImage, ...] = (),
        keyboard: Keyboard | None = None,
    ) -> OutboundMessage:
        return cls(tuple(blocks), images, keyboard)


def render_plain(message: OutboundMessage) -> str:
    """Unstyled text. Used by tests, by the CLI, and as any channel's fallback.

    Every renderer must agree with this one about *content*; they differ only in
    how they decorate it.
    """
    parts: list[str] = []
    for block in message.blocks:
        match block.kind:
            case BlockKind.HEADING:
                parts.append(block.text)
            case BlockKind.TEXT | BlockKind.DISCLAIMER:
                parts.append(block.text)
            case BlockKind.BULLETS:
                parts.append("\n".join(f"• {item}" for item in block.items))
            case BlockKind.STEPS:
                parts.append(
                    "\n".join(f"{i}. {item}" for i, item in enumerate(block.items, start=1))
                )
            case BlockKind.KEY_VALUE:
                parts.append("\n".join(f"{k}\n{v}" for k, v in block.pairs))
            case BlockKind.DIVIDER:
                parts.append("—" * 20)
    return "\n\n".join(p for p in parts if p)


class ChannelAdapter(ABC):
    """What a transport must implement. Two methods; the rest is the channel's own."""

    channel: Channel

    @abstractmethod
    def render(self, message: OutboundMessage) -> object:
        """Turn an OutboundMessage into whatever this transport sends."""

    @abstractmethod
    async def send(self, user_id: str, message: OutboundMessage) -> None:
        """Deliver it."""
