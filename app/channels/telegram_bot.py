"""Telegram transport. The only file in the project that imports `telegram`.

Two halves:

*The renderer* turns an `OutboundMessage` into Telegram HTML. HTML rather than
MarkdownV2 because MarkdownV2 requires escaping sixteen characters, and a scam
message quoted back to a user is exactly the kind of text that contains all
sixteen. One missed escape is a `Can't parse entities` error in front of a user.

*The handlers* do routing and nothing else. Every one of them normalises the
update into an `InboundMessage` and hands off. The feature engines land in
Phases 3–6 behind `_dispatch`, and none of them will need to be told what a
`chat_id` is.

Run it in development with:

    python -m app.channels.telegram_bot

Polling, because it needs no tunnel and no public URL on a Windows laptop.
Deployment uses webhook mode, served by `app.main` — see `TELEGRAM_MODE`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import html
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.channels import copy
from app.channels.base import (
    Attachment,
    AttachmentKind,
    Block,
    BlockKind,
    Button,
    ChannelAdapter,
    InboundMessage,
    Intent,
    Keyboard,
    OutboundMessage,
    classify,
)
from app.config import Settings, get_settings
from app.features.budget.parser import looks_like_budget_input
from app.llm import spend
from app.llm.base import CapabilityError, SpendCapReached
from app.llm.localise import localise, needs_translation
from app.models import repo
from app.models.db import Profile, delete_user
from app.models.enums import (
    AgeBand,
    Area,
    CasteCategory,
    Channel,
    Feature,
    Gender,
    IncomeBand,
    Language,
    NeedCategory,
)
from app.models.session import session_scope

__all__ = [
    "TELEGRAM_MAX_MESSAGE_CHARS",
    "TelegramAdapter",
    "build_application",
    "render_html",
    "run_polling",
    "webhook_secret_token",
]

log = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_CHARS = 4096

# §8: "No message longer than roughly two phone screens." Telegram's own ceiling is
# 4096, which is far past readable; we split well before it and let the natural
# block boundaries decide where.
_SOFT_SPLIT_CHARS = 1800

DELETE_CONFIRM = "profile_delete_confirm"
DELETE_DO = "profile_delete_do"
LANG_PREFIX = "lang_"
SCHEME_PREFIX = "scheme:"


_STATES: tuple[tuple[str, str], ...] = (
    ("Andhra Pradesh", "andhra_pradesh"),
    ("Arunachal Pradesh", "arunachal_pradesh"),
    ("Assam", "assam"),
    ("Bihar", "bihar"),
    ("Chhattisgarh", "chhattisgarh"),
    ("Goa", "goa"),
    ("Gujarat", "gujarat"),
    ("Haryana", "haryana"),
    ("Himachal Pradesh", "himachal_pradesh"),
    ("Jharkhand", "jharkhand"),
    ("Karnataka", "karnataka"),
    ("Kerala", "kerala"),
    ("Madhya Pradesh", "madhya_pradesh"),
    ("Maharashtra", "maharashtra"),
    ("Manipur", "manipur"),
    ("Meghalaya", "meghalaya"),
    ("Mizoram", "mizoram"),
    ("Nagaland", "nagaland"),
    ("Odisha", "odisha"),
    ("Punjab", "punjab"),
    ("Rajasthan", "rajasthan"),
    ("Sikkim", "sikkim"),
    ("Tamil Nadu", "tamil_nadu"),
    ("Telangana", "telangana"),
    ("Tripura", "tripura"),
    ("Uttar Pradesh", "uttar_pradesh"),
    ("Uttarakhand", "uttarakhand"),
    ("West Bengal", "west_bengal"),
    ("Andaman & Nicobar", "andaman_nicobar"),
    ("Chandigarh", "chandigarh"),
    ("Dadra & Nagar Haveli and Daman & Diu", "dadra_nagar_haveli_daman_diu"),
    ("Delhi", "delhi"),
    ("Jammu & Kashmir", "jammu_kashmir"),
    ("Ladakh", "ladakh"),
    ("Lakshadweep", "lakshadweep"),
    ("Puducherry", "puducherry"),
)
_STATE_VALUES = {value: label for label, value in _STATES}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _block_html(block: Block) -> str:
    match block.kind:
        case BlockKind.HEADING:
            return f"<b>{_esc(block.text)}</b>"
        case BlockKind.TEXT:
            return _esc(block.text)
        case BlockKind.DISCLAIMER:
            return f"<i>{_esc(block.text)}</i>"
        case BlockKind.BULLETS:
            return "\n".join(f"• {_esc(item)}" for item in block.items)
        case BlockKind.STEPS:
            return "\n".join(
                f"<b>{i}.</b> {_esc(item)}" for i, item in enumerate(block.items, start=1)
            )
        case BlockKind.KEY_VALUE:
            # Value on its own line — the design rule from §8.
            return "\n".join(f"<b>{_esc(k)}</b>\n{_esc(v)}" for k, v in block.pairs)
        case BlockKind.DIVIDER:
            return "─────────────"
    return ""


def render_html(message: OutboundMessage) -> list[str]:
    """Blocks to Telegram HTML, split into sendable chunks on block boundaries.

    Returns at least one string, so a caller never has to handle the empty case.
    """
    rendered = [html_text for block in message.blocks if (html_text := _block_html(block))]
    if not rendered:
        return [""]

    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for part in rendered:
        addition = len(part) + 2
        if current and length + addition > _SOFT_SPLIT_CHARS:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(part)
        length += addition
    if current:
        chunks.append("\n\n".join(current))

    # A single block longer than Telegram's hard limit still has to be cut.
    safe: list[str] = []
    for chunk in chunks:
        while len(chunk) > TELEGRAM_MAX_MESSAGE_CHARS:
            cut = chunk.rfind("\n", 0, TELEGRAM_MAX_MESSAGE_CHARS) or TELEGRAM_MAX_MESSAGE_CHARS
            safe.append(chunk[:cut])
            chunk = chunk[cut:].lstrip("\n")
        safe.append(chunk)
    return safe


def render_keyboard(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if keyboard is None or not keyboard.rows:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(button.label, url=button.url)
                if button.url
                else InlineKeyboardButton(button.label, callback_data=button.action)
                for button in row
            ]
            for row in keyboard.rows
        ]
    )


class TelegramAdapter(ChannelAdapter):
    """Renders and delivers. Holds a bot, holds no state."""

    channel = Channel.TELEGRAM

    def __init__(self, bot) -> None:  # telegram.Bot — untyped to keep imports light
        self._bot = bot

    def render(self, message: OutboundMessage) -> list[str]:
        return render_html(message)

    async def send(self, user_id: str, message: OutboundMessage) -> None:
        chunks = render_html(message)
        markup = render_keyboard(message.keyboard)
        for index, chunk in enumerate(chunks):
            last_text_chunk = index == len(chunks) - 1 and not message.images
            await self._bot.send_message(
                chat_id=user_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=markup if last_text_chunk else None,
            )
        for position, image in enumerate(message.images):
            await self._bot.send_photo(
                chat_id=user_id,
                photo=image.png,
                caption=_esc(image.caption) or None,
                parse_mode=ParseMode.HTML,
                reply_markup=markup if position == len(message.images) - 1 else None,
            )


async def _reply(update: Update, message: OutboundMessage) -> None:
    """Answer in the chat the update came from."""
    chat = update.effective_chat
    if chat is None:  # pragma: no cover - Telegram always sets this for our handlers
        return
    await TelegramAdapter(chat.get_bot()).send(str(chat.id), message)


async def _reply_analysis(update: Update, message: OutboundMessage) -> None:
    """Reply with *computed* output, translated if the user does not read English.

    Only the engines' output goes through here. The templated copy in `copy.py` is
    deliberately excluded: it is already hand-written in Hindi, and §8's three trust
    promises — "I will never ask for your OTP" — are asserted by tests word for word.
    Sending those through a model to be re-worded would put the one paragraph the
    product cannot get wrong at the mercy of a paraphrase.

    The language is read here rather than passed in, so a new analysis path cannot
    forget to translate. Translation never blocks the answer: any failure inside
    `localise` returns the English, which still goes out.
    """
    telegram_id = str(update.effective_user.id) if update.effective_user else "unknown"

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        language = user.language
        if not needs_translation(language.value):
            await _reply(update, message)
            return

        provider = get_provider_or_none()
        if provider is not None:
            try:
                spend.check_caps(session, user)
            except SpendCapReached:
                provider = None

        if provider is not None:
            message = await asyncio.to_thread(localise, message, language.value, provider)
            spend.record_call(session, provider, method="analyse", feature=Feature.TRANSLATION)

    await _reply(update, message)


# ---------------------------------------------------------------------------
# Inbound normalisation
# ---------------------------------------------------------------------------


def _attachment_kind(mime_type: str | None, filename: str | None) -> AttachmentKind:
    mime = (mime_type or "").lower()
    name = (filename or "").lower()
    if mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return AttachmentKind.IMAGE
    if mime == "application/pdf" or name.endswith(".pdf"):
        return AttachmentKind.PDF
    return AttachmentKind.OTHER


async def _download(file_id: str, context: ContextTypes.DEFAULT_TYPE) -> bytes:
    """Pull the bytes into memory. They are never written to disk — §10."""
    telegram_file = await context.bot.get_file(file_id)
    return bytes(await telegram_file.download_as_bytearray())


def _is_forwarded(update: Update) -> bool:
    """Forwarding is the behaviour scam detection exists to interrupt.

    `forward_origin` is the v7+ field; `forward_date` covers older library payloads.
    """
    message = update.effective_message
    if message is None:
        return False
    return bool(getattr(message, "forward_origin", None) or getattr(message, "forward_date", None))


async def _to_inbound(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    command: str | None = None,
    download: bool = True,
) -> InboundMessage:
    message = update.effective_message
    user = update.effective_user
    text = (message.text or message.caption or "") if message else ""
    files: list[Attachment] = []
    settings = get_settings()

    if message is not None and download:
        if message.photo:
            largest = message.photo[-1]
            files.append(
                Attachment(
                    kind=AttachmentKind.IMAGE,
                    content=await _download(largest.file_id, context),
                    filename=f"{largest.file_unique_id}.jpg",
                    mime_type="image/jpeg",
                )
            )
        document = message.document
        if document is not None:
            kind = _attachment_kind(document.mime_type, document.file_name)
            oversized = (document.file_size or 0) > settings.max_upload_mb * 1024 * 1024
            if kind is not AttachmentKind.OTHER and not oversized:
                files.append(
                    Attachment(
                        kind=kind,
                        content=await _download(document.file_id, context),
                        filename=document.file_name,
                        mime_type=document.mime_type,
                    )
                )

    with session_scope() as session:
        record = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=str(user.id if user else "unknown")
        )
        language = record.language

    return InboundMessage(
        user_id=str(user.id if user else "unknown"),
        channel=Channel.TELEGRAM,
        intent=classify(
            command=command,
            text=text,
            files=tuple(files),
            forwarded=_is_forwarded(update),
        ),
        text=text,
        files=tuple(files),
        forwarded=_is_forwarded(update),
        language=language,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _Step:
    """One question in the scheme questionnaire.

    The questions are data rather than a chain of `if`s for one specific reason: the
    step number is now the index in `_STEPS`, so it cannot disagree with the number of
    questions. It used to — location was asked twice and both taps said "Question 3 of
    6", so a user answered "3 of 6" twice and then watched it jump to 4. A progress
    counter that lies is worse than none, because it is the one thing telling somebody
    how much longer this takes.
    """

    field: str  # the callback token, and the key `_save_scheme_answer` parses
    attribute: str  # the column on `Profile`
    title: str
    question: str
    options: tuple[tuple[str, str], ...]
    columns: int = 2

    def is_answered(self, profile: Profile) -> bool:
        value = getattr(profile, self.attribute)
        return bool(value) if self.attribute == "needs" else value is not None


_STEPS: tuple[_Step, ...] = (
    _Step(
        field="gender",
        attribute="gender",
        title="Gender",
        question="Which option should I use when checking eligibility?",
        options=(
            ("Woman", "female"),
            ("Man", "male"),
            ("Another gender", "other"),
            ("Prefer not to say", "prefer_not_to_say"),
        ),
    ),
    _Step(
        field="age",
        attribute="age_band",
        title="Age",
        question="What is your age group?",
        options=(
            ("Under 18", "under_18"),
            ("18–25", "18_25"),
            ("26–35", "26_35"),
            ("36–45", "36_45"),
            ("46–60", "46_60"),
            ("Over 60", "over_60"),
            ("Prefer not to say", "prefer_not_to_say"),
        ),
    ),
    _Step(
        field="state",
        attribute="state",
        title="State",
        question="Which state or union territory do you live in?",
        options=(*_STATES, ("Prefer not to say", "prefer_not_to_say")),
    ),
    _Step(
        field="area",
        attribute="area",
        title="Village or city",
        question="Do you live in a village or a city/town?",
        options=(
            ("Village / rural area", "rural"),
            ("City / town", "urban"),
            ("Prefer not to say", "prefer_not_to_say"),
        ),
        columns=1,
    ),
    _Step(
        field="income",
        attribute="income_band",
        title="Household income",
        question="About how much does your whole household receive each month?",
        options=(
            ("Under ₹10,000", "under_10k"),
            ("₹10,000–₹25,000", "10k_25k"),
            ("₹25,000–₹50,000", "25k_50k"),
            ("₹50,000–₹1 lakh", "50k_1l"),
            ("Over ₹1 lakh", "over_1l"),
            ("Prefer not to say", "prefer_not_to_say"),
        ),
        columns=1,
    ),
    _Step(
        field="need",
        attribute="needs",
        title="What you need",
        question="What would you most like help with right now?",
        options=(
            ("Education", "education"),
            ("Starting a business", "business"),
            ("Housing", "housing"),
            ("Health", "health"),
            ("Pension", "pension"),
            ("Farming", "farming"),
            ("Savings", "savings"),
        ),
    ),
    _Step(
        field="category",
        attribute="category",
        title="Category",
        question="Which category should I use for eligibility?",
        options=(
            ("General", "general"),
            ("OBC", "obc"),
            ("SC", "sc"),
            ("ST", "st"),
            ("Prefer not to say", "prefer_not_to_say"),
        ),
    ),
)

SCHEME_BACK = "back"


def _progress(index: int) -> str:
    """"Step 3 of 7  ●●●○○○○" — where they are and how much is left, at a glance."""
    done = index + 1
    return f"Step {done} of {len(_STEPS)}  {'●' * done}{'○' * (len(_STEPS) - done)}"


def _scheme_question(profile: Profile) -> OutboundMessage | None:
    """The next unanswered question, or `None` when every one is done.

    Each answer is persisted as it is tapped, so there is no volatile conversation
    state to lose if the process restarts or the user comes back another day. Every
    question carries a Back button, because the alternative for a mistap was redoing
    all seven.
    """
    for index, step in enumerate(_STEPS):
        if step.is_answered(profile):
            continue

        rows = [
            [
                Button(label=label, action=f"{SCHEME_PREFIX}{step.field}:{value}")
                for label, value in step.options[position : position + step.columns]
            ]
            for position in range(0, len(step.options), step.columns)
        ]
        if index > 0:
            rows.append([Button(label="← Back", action=f"{SCHEME_PREFIX}{SCHEME_BACK}")])

        return OutboundMessage.of(
            Block.heading(f"{_progress(index)} · {step.title}"),
            Block.para(step.question),
            keyboard=Keyboard.of(*rows),
        )
    return None


def _scheme_step_back(profile: Profile) -> OutboundMessage | None:
    """Clear the most recent answer and ask it again."""
    for step in reversed(_STEPS):
        if step.is_answered(profile):
            setattr(profile, step.attribute, [] if step.attribute == "needs" else None)
            break
    return _scheme_question(profile)


def _pretty_enum(value: object) -> str:
    raw = str(getattr(value, "value", value))
    return (
        raw.replace("_", " ")
        .replace("10k", "₹10k")
        .replace("25k", "₹25k")
        .replace("50k", "₹50k")
        .replace("1l", "₹1 lakh")
        .title()
    )


def _scheme_profile_saved(profile: Profile) -> OutboundMessage:
    """Read the six answers back before matching, so a wrong tap is visible.

    Sent as its own message ahead of the results: matching does database work and a
    model call, and a user who has just tapped six buttons should not watch a blank
    screen wondering whether it worked.
    """
    state = _STATE_VALUES.get(profile.state or "", (profile.state or "").replace("_", " ").title())
    need = _pretty_enum(profile.needs[0]) if profile.needs else "Not stated"
    return OutboundMessage.of(
        Block.heading("Your answers are saved"),
        Block.key_values(
            (
                ("Gender", _pretty_enum(profile.gender)),
                ("Age", _pretty_enum(profile.age_band)),
                ("Location", f"{state} · {_pretty_enum(profile.area)}"),
                ("Monthly household income", _pretty_enum(profile.income_band)),
                ("Help needed", need),
                ("Category", _pretty_enum(profile.category)),
            )
        ),
        Block.para("Checking which schemes you qualify for…"),
    )


async def _run_scheme_matching(update: Update, telegram_id: str) -> None:
    """Filter, rank, explain, reply. Called once the six answers are in."""
    from app.features.schemes.schema import SchemeProfile
    from app.features.schemes.service import find_schemes

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        profile = repo.get_or_create_profile(session, user)
        await _reply(update, _scheme_profile_saved(profile))

        scheme_profile = SchemeProfile.from_row(profile)
        try:
            provider = get_provider_or_none()
        except Exception:  # pragma: no cover - defensive; matching works without it
            provider = None

        response = await find_schemes(
            scheme_profile, session=session, user=user, provider=provider
        )
    await _reply_analysis(update, response)


def get_provider_or_none():
    """The text provider, or `None` when there is no key or it is misconfigured.

    Schemes are matched from the database either way; the model only rewrites the
    record's own words into plainer language.
    """
    from app.llm.base import LLMError
    from app.llm.factory import get_provider

    try:
        return get_provider()
    except LLMError:
        return None


def _start_or_resume_scheme_profile(telegram_id: str) -> OutboundMessage | None:
    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        profile = repo.get_or_create_profile(session, user)
        return _scheme_question(profile)


def _save_scheme_answer(telegram_id: str, action: str) -> OutboundMessage | None:
    token = action.removeprefix(SCHEME_PREFIX)

    # "← Back" undoes the last answer instead of carrying a value of its own.
    if token == SCHEME_BACK:
        with session_scope() as session:
            user = repo.get_or_create_user(
                session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
            )
            profile = repo.get_or_create_profile(session, user)
            question = _scheme_step_back(profile)
            session.commit()
            return question

    try:
        field, raw_value = token.split(":", 1)
    except ValueError:
        return copy.fallback()

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        profile = repo.get_or_create_profile(session, user)
        try:
            if field == "gender":
                profile.gender = Gender(raw_value)
            elif field == "age":
                profile.age_band = AgeBand(raw_value)
            elif field == "state" and raw_value in {*_STATE_VALUES, "prefer_not_to_say"}:
                profile.state = raw_value
            elif field == "area":
                profile.area = Area(raw_value)
            elif field == "income":
                profile.income_band = IncomeBand(raw_value)
            elif field == "need":
                profile.needs = [NeedCategory(raw_value).value]
            elif field == "category":
                profile.category = CasteCategory(raw_value)
            else:
                return copy.fallback(user.language)
        except ValueError:
            return copy.fallback(user.language)
        session.commit()
        return _scheme_question(profile)


def _restart_scheme_profile(telegram_id: str) -> OutboundMessage | None:
    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        profile = repo.get_or_create_profile(session, user)
        profile.gender = None
        profile.age_band = None
        profile.state = None
        profile.area = None
        profile.income_band = None
        profile.category = None
        profile.needs = []
        session.commit()
        return _scheme_question(profile)


def _dispatch(inbound: InboundMessage) -> OutboundMessage:
    """Intent to response.

    Phases 3–6 replace each branch with a real engine call. The routing under test
    today is the routing that ships — only the bodies change.
    """
    match inbound.intent:
        case Intent.START:
            return copy.welcome(inbound.language)
        case Intent.HELP:
            return copy.help_message(inbound.language)
        case Intent.SCAM_CHECK | Intent.LOAN_ANALYSIS | Intent.SCHEMES | Intent.BUDGET:
            return copy.prompt_for(inbound.intent.value, inbound.language)
        case _:
            return copy.fallback(inbound.language)


async def _handle_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> None:
    inbound = await _to_inbound(update, context, command=command, download=False)
    await _reply(update, _dispatch(inbound))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "start")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "help")


async def scam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "scam_check")


async def loan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "loan_analysis")


async def schemes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    telegram_id = str(user.id if user else "unknown")

    question = _start_or_resume_scheme_profile(telegram_id)
    if question is None:
        # Already answered — go straight to results rather than asking again.
        await _run_scheme_matching(update, telegram_id)
        return
    await _reply(update, question)


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "budget")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show everything stored about this user, and offer to delete it.

    `/start` promises the user can delete what we keep. A promise with no button
    behind it is worse than no promise, so the delete is wired now rather than in
    the phase that fills the profile in.
    """
    user = update.effective_user
    telegram_id = str(user.id if user else "unknown")
    with session_scope() as session:
        record = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        profile = session.get(Profile, record.id)
        language = record.language
        created = record.created_at
        pairs: list[tuple[str, str]] = [
            ("First seen", created.strftime("%d %B %Y")),
            ("Language", "हिंदी" if language is Language.HINDI else "English"),
        ]
        if profile is None:
            pairs.append(("Profile", "nothing saved yet"))
        else:
            for label, value in (
                ("Gender", profile.gender),
                ("Age", profile.age_band),
                ("State", profile.state),
                ("Area", profile.area),
                ("Income band", profile.income_band),
                ("Category", profile.category),
            ):
                pairs.append((label, str(value.value if hasattr(value, "value") else value or "—")))

    await _reply(
        update,
        OutboundMessage.of(
            Block.heading("What I have saved about you"),
            Block.para("No name. No phone number. No document you ever sent me."),
            Block.key_values(pairs),
            Block.disclaimer(copy.DISCLAIMER),
            keyboard=Keyboard.of([Button(label="🗑️ Delete everything", action=DELETE_CONFIRM)]),
        ),
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        OutboundMessage.of(
            Block.para("Which language should I use?"),
            keyboard=Keyboard.of(
                [
                    Button(label="English", action=f"{LANG_PREFIX}en"),
                    Button(label="हिंदी", action=f"{LANG_PREFIX}hi"),
                ]
            ),
        ),
    )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any photo is a scam check — §8's zero-command path, now with an engine behind it.

    The bytes are pulled into memory, analysed, and dropped when this function
    returns. They are never written to disk; what survives is a content hash.
    """
    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    inbound = await _to_inbound(update, context, download=True)
    if not inbound.files:
        await _reply(update, copy.prompt_for(Intent.SCAM_CHECK.value, inbound.language))
        return

    await _run_scam_check(update, inbound.files[0].content, inbound.user_id)


async def _run_scam_check(update: Update, image: bytes, telegram_id: str) -> None:
    """Shared by the photo path and the "check this image" correction button."""
    from app.features.scam.service import check_screenshot
    from app.llm.factory import get_vision_provider

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        response = await check_screenshot(
            image, session=session, user=user, provider=get_vision_provider()
        )
    await _reply_analysis(update, response)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    document = message.document if message else None
    chat = update.effective_chat
    settings = get_settings()

    if document is not None and (document.file_size or 0) > settings.max_upload_mb * 1024 * 1024:
        await _reply(
            update,
            OutboundMessage.of(
                Block.para(
                    f"That file is bigger than {settings.max_upload_mb} MB, which is more than "
                    "I can read. Send just the pages with the loan terms on them, or a photo "
                    "of the important page."
                ),
                keyboard=copy.main_keyboard(),
            ),
        )
        return

    kind = _attachment_kind(
        document.mime_type if document else None, document.file_name if document else None
    )
    if kind is AttachmentKind.OTHER:
        await _reply(
            update,
            OutboundMessage.of(
                Block.para(
                    "I can read PDFs and photos. If that is a loan paper, send it as a PDF "
                    "or take a clear photo of it."
                ),
                keyboard=copy.main_keyboard(),
            ),
        )
        return

    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    inbound = await _to_inbound(update, context, download=True)
    if not inbound.files:
        intent = Intent.LOAN_ANALYSIS if kind is AttachmentKind.PDF else Intent.SCAM_CHECK
        await _reply(update, _dispatch(dataclasses.replace(inbound, intent=intent)))
        return

    attachment = inbound.files[0]
    if attachment.kind is AttachmentKind.PDF:
        await _run_loan_analysis(update, attachment.content, inbound.user_id, attachment.filename)
    else:
        await _run_scam_check(update, attachment.content, inbound.user_id)


async def _run_loan_analysis(
    update: Update, pdf: bytes, telegram_id: str, filename: str | None
) -> None:
    from app.features.loan.service import check_document
    from app.llm.factory import get_vision_provider

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        response = await check_document(
            pdf,
            session=session,
            user=user,
            provider=get_vision_provider(),
            filename=filename or "",
        )
    await _reply_analysis(update, response)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inbound = await _to_inbound(update, context, download=False)

    if inbound.text.strip().lower() in {"ok", "okay", "yes", "haan", "ha", "continue"}:
        pending_question = None
        with session_scope() as session:
            user = repo.get_or_create_user(
                session,
                channel=Channel.TELEGRAM,
                channel_user_id=inbound.user_id,
            )
            profile = session.get(Profile, user.id)
            if profile is not None and (
                profile.gender is None
                or profile.age_band is None
                or profile.state is None
                or profile.area is None
                or profile.income_band is None
                or not profile.needs
                or profile.category is None
            ):
                pending_question = _scheme_question(profile)
        if pending_question is not None:
            await _reply(update, pending_question)
            return

    # A forwarded message is the behaviour scam detection exists to interrupt, and
    # text needs no vision call — so it is checked directly, for free, right here.
    if inbound.intent is Intent.SCAM_CHECK and inbound.text.strip():
        await _run_text_scam_check(update, inbound.text, inbound.user_id)
        return

    # Numbers with money words next to them — §8's zero-command path for the budget
    # coach. `looks_like_budget_input` is stricter than the intent classifier: the
    # classifier decides whether to *offer* the coach, this decides whether to run it.
    if inbound.intent is Intent.BUDGET and looks_like_budget_input(inbound.text):
        await _run_budget(update, inbound.text, inbound.user_id, inbound.language)
        return

    # Anything else that is actual text — "hi", "someone wants my OTP", a question in
    # Tamil — is read by the model and answered in the user's own language, instead of
    # met with the four buttons. The buttons remain the fallback when there is no key.
    if inbound.text.strip():
        await _run_concierge(update, inbound.text, inbound.user_id)
        return

    await _reply(update, _dispatch(inbound))


async def _run_concierge(update: Update, text: str, telegram_id: str) -> None:
    """Read a free-text message, route it, and reply in the user's language.

    Router only: the model may greet and point at a tool, never quote a figure or say
    whether a deal is good — the analysis itself still runs in the tested engines when
    the user sends the screenshot, PDF or numbers.
    """
    from app.features.concierge import concierge_response

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    provider = get_provider_or_none()
    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        if provider is not None:
            try:
                spend.check_caps(session, user)
            except SpendCapReached:
                provider = None

        response, language = await asyncio.to_thread(concierge_response, text, provider)

        if provider is not None:
            spend.record_call(session, provider, method="analyse", feature=Feature.CONCIERGE)
        # Persist en/hi so the templated parts of later replies follow the same
        # language the user just wrote in.
        if language in ("en", "hi"):
            repo.set_language(
                session, user, Language.HINDI if language == "hi" else Language.ENGLISH
            )

    await _reply(update, response)


async def _run_budget(update: Update, text: str, telegram_id: str, language: Language) -> None:
    """The budget coach. No model call, so nothing to cap and nothing to spend."""
    from app.features.budget.service import check_budget_text

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.UPLOAD_PHOTO)

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        response = await check_budget_text(
            text, session=session, user=user, language=language.value
        )
    await _reply_analysis(update, response)


async def _run_text_scam_check(update: Update, text: str, telegram_id: str) -> None:
    """Rules over typed or forwarded text. No image, so no vision cost."""
    from app.features.scam.formatter import format_assessment
    from app.features.scam.service import analyse_text
    from app.llm.factory import get_provider

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    with session_scope() as session:
        user = repo.get_or_create_user(
            session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
        )
        try:
            spend.check_caps(session, user)
            provider = get_provider()
        except (SpendCapReached, CapabilityError):
            # The rules are free and run without a provider. Losing the model's
            # opinion costs 40% of the blend, not the feature.
            provider = None

        assessment = await asyncio.to_thread(analyse_text, text, provider, session)

    await _reply_analysis(update, format_assessment(assessment, text))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-button taps. Same dispatch as commands wherever the token is an intent."""
    query = update.callback_query
    if query is None:  # pragma: no cover
        return
    await query.answer()
    action = query.data or ""
    telegram_id = str(update.effective_user.id) if update.effective_user else "unknown"

    if action == Intent.SCHEMES.value:
        await _reply(update, _start_or_resume_scheme_profile(telegram_id))
        return

    if action == f"{SCHEME_PREFIX}restart":
        question = _restart_scheme_profile(telegram_id)
        await _reply(update, question if question is not None else copy.fallback())
        return

    if action.startswith(SCHEME_PREFIX):
        question = _save_scheme_answer(telegram_id, action)
        if question is None:
            await _run_scheme_matching(update, telegram_id)
            return
        await _reply(update, question)
        return

    if action.startswith(LANG_PREFIX):
        chosen = Language(action.removeprefix(LANG_PREFIX))
        with session_scope() as session:
            record = repo.get_or_create_user(
                session, channel=Channel.TELEGRAM, channel_user_id=telegram_id
            )
            repo.set_language(session, record, chosen)
        note = (
            "ठीक है — भाषा सहेज ली गई। पूरा हिंदी अनुवाद अगले अपडेट में आ रहा है; "
            "तब तक मैं अंग्रेज़ी में जवाब दूँगा."
            if chosen is Language.HINDI
            else "Done — I will reply in English."
        )
        await _reply(update, OutboundMessage.of(Block.para(note), keyboard=copy.main_keyboard()))
        return

    if action == DELETE_CONFIRM:
        await _reply(
            update,
            OutboundMessage.of(
                Block.para(
                    "This deletes your profile, your budgets and every past analysis. "
                    "It cannot be undone. Are you sure?"
                ),
                keyboard=Keyboard.of(
                    [
                        Button(label="Yes, delete it all", action=DELETE_DO),
                        Button(label="No, keep it", action="start"),
                    ]
                ),
            ),
        )
        return

    if action == DELETE_DO:
        with session_scope() as session:
            delete_user(session, channel=Channel.TELEGRAM, channel_user_id=telegram_id)
        await _reply(
            update,
            OutboundMessage.of(
                Block.para(
                    "Deleted. Nothing of yours is left. Send /start whenever you want to "
                    "begin again."
                )
            ),
        )
        return

    inbound = await _to_inbound(update, context, command=action, download=False)
    await _reply(update, _dispatch(inbound))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("scam", scam_command))
    application.add_handler(CommandHandler("loan", loan_command))
    application.add_handler(CommandHandler("schemes", schemes_command))
    application.add_handler(CommandHandler("budget", budget_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("language", language_command))

    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """§8: never a naked error message, always a next action.

    The exception goes to the log. The user gets a sentence and four buttons.
    """
    log.exception("handler failed", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat is not None:
        try:
            await TelegramAdapter(update.effective_chat.get_bot()).send(
                str(update.effective_chat.id),
                OutboundMessage.of(
                    Block.para(
                        "Something went wrong on my side — not on yours, and nothing you sent "
                        "was saved. Try again, or pick one of these:"
                    ),
                    keyboard=copy.main_keyboard(),
                ),
            )
        except Exception:  # pragma: no cover - the error path must not raise
            log.exception("failed to deliver the error message")


def build_application(settings: Settings | None = None) -> Application:
    """Construct the PTB application. Raises if there is no token to build it with."""
    settings = settings or get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather and put it in .env"
        )
    token = settings.telegram_bot_token.get_secret_value()
    application = Application.builder().token(token).build()
    register_handlers(application)
    return application


def webhook_secret_token(settings: Settings | None = None) -> str:
    """A secret derived from the bot token, so webhook mode needs no extra config.

    Telegram echoes it back in `X-Telegram-Bot-Api-Secret-Token`; `app.main` rejects
    any POST that does not carry it. Without this the webhook URL is a public
    endpoint that anyone who guesses it can feed fake updates to.
    """
    settings = settings or get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    raw = settings.telegram_bot_token.get_secret_value().encode("utf-8")
    return hashlib.sha256(b"arthasathi-webhook:" + raw).hexdigest()[:48]


def _silence_token_leaking_loggers() -> None:
    """Stop httpx printing the bot token into every log line.

    Telegram puts the token in the URL path — `api.telegram.org/bot<TOKEN>/getMe` —
    and httpx logs the full URL at INFO. Left alone, the credential ends up in the
    console, in log files, and in whatever ships those logs. Anyone holding it can
    read every message sent to the bot and reply as it.

    httpcore is silenced for the same reason.
    """
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_polling(settings: Settings | None = None) -> None:
    """Development entry point. No tunnel, no public URL, works behind any router."""
    from app.models.session import init_db

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )
    _silence_token_leaking_loggers()
    # Token first: a missing token should not leave a database file behind.
    application = build_application(settings)

    # And a provider that cannot see should fail here, not in front of a user.
    from app.llm.factory import verify_configuration

    verify_configuration(settings)

    init_db()

    # Python 3.14 removed the implicit event-loop creation that
    # `asyncio.get_event_loop()` used to do outside a running loop. PTB v21's
    # `run_polling` still relies on it, so it raises "There is no current event
    # loop" the moment it starts. Creating one first is the whole fix; drop this
    # when python-telegram-bot catches up.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    log.info("ArthaSathi bot starting in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":  # pragma: no cover
    run_polling()
