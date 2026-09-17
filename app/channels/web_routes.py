"""The web dashboard routes. Same engines as Telegram, a browser instead of a chat.

Every page ends in an `OutboundMessage` produced by the very functions the bot calls
— `check_screenshot`, `check_document`, `find_schemes`, `check_budget_text`. There is
no second scorer and no second finance engine here; `web.py` turns that message into
`RenderedBlock`s and a template prints them. §9: *"same feature engines, zero
duplicated logic."*

Two things this layer owns that the bot does not:

- **Uploads arrive over HTTP**, so the size limit is enforced here, before the bytes
  reach a feature. A feature trusts that its caller already refused a 2 GB "PDF".
- **There is no chat to hold state**, so each request is self-contained: the schemes
  form posts all six answers at once instead of the bot's six-turn conversation.

Identity is a random opaque cookie — no login, no name, nothing that ties a session to
a person. It exists only so one browser's spend cap and cache are its own.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.channels import copy
from app.channels.base import Block, OutboundMessage
from app.channels.regions import INDIAN_STATES
from app.channels.web import WebAdapter, plain_text
from app.config import get_settings
from app.features.budget.service import check_budget_text
from app.features.loan.service import check_document
from app.features.scam.formatter import format_assessment
from app.features.scam.service import analyse_text
from app.features.schemes.schema import SchemeProfile
from app.features.schemes.service import find_schemes
from app.models import repo
from app.models.enums import (
    AgeBand,
    Area,
    CasteCategory,
    Channel,
    Gender,
    IncomeBand,
    Language,
    NeedCategory,
)
from app.models.session import session_scope

__all__ = ["router", "templates"]

templates = Jinja2Templates(directory="app/templates")
_adapter = WebAdapter()

router = APIRouter()

# The dropdowns on the schemes form. Each is (submitted value, shown label); "" is the
# "prefer not to say" option, which the matcher reads as unanswered — declining widens
# results rather than narrowing them (§6).
_GENDERS = (("female", "Female"), ("male", "Male"), ("other", "Other"))
_AGES = (
    ("under_18", "Under 18"),
    ("18_25", "18–25"),
    ("26_35", "26–35"),
    ("36_45", "36–45"),
    ("46_60", "46–60"),
    ("over_60", "Over 60"),
)
_AREAS = (("rural", "Village / rural"), ("urban", "Town / city"))
_INCOMES = (
    ("under_10k", "Under ₹10,000"),
    ("10k_25k", "₹10,000 – ₹25,000"),
    ("25k_50k", "₹25,000 – ₹50,000"),
    ("50k_1l", "₹50,000 – ₹1 lakh"),
    ("over_1l", "Over ₹1 lakh"),
)
_CASTES = (
    ("general", "General"),
    ("obc", "OBC"),
    ("sc", "SC"),
    ("st", "ST"),
)
_NEEDS = (
    ("education", "Education"),
    ("business", "Business"),
    ("housing", "Housing"),
    ("health", "Health"),
    ("pension", "Pension"),
    ("farming", "Farming"),
    ("savings", "Savings"),
)


# ---------------------------------------------------------------------------
# Rendering plumbing
# ---------------------------------------------------------------------------


def _lang(request: Request) -> Language:
    code = request.query_params.get("lang") or request.cookies.get("lang") or "en"
    return Language.HINDI if code == "hi" else Language.ENGLISH


def _page(request: Request, template: str, **context) -> HTMLResponse:
    """Render a template with the per-request language bound into `t` and `url_for`.

    `t` is bound to the current language so a template writes `t('nav_scam')` without
    threading the language through every call. The language cookie is (re)set on every
    render so a `?lang=` toggle sticks as the user moves between pages.
    """
    language = _lang(request)
    ctx = {
        "lang": language.value,
        "t": lambda key: copy.t(key, language),
        "states": INDIAN_STATES,
        **context,
    }
    response = templates.TemplateResponse(request, template, ctx)
    response.set_cookie("lang", language.value, max_age=31_536_000, samesite="lax")
    return response


def _result(request: Request, message: OutboundMessage, *, feature: str) -> HTMLResponse:
    rendered = _adapter.render(message)
    return _page(
        request,
        "result.html",
        feature=feature,
        blocks=rendered["blocks"],
        images=rendered["images"],
        links=rendered["links"],
        plain=plain_text(message),
    )


def _visitor_id(request: Request) -> str:
    """A stable-per-browser opaque id, or a fresh one to be set as a cookie."""
    return request.cookies.get("visitor") or f"web-{uuid.uuid4().hex[:16]}"


def _with_visitor(response: HTMLResponse, visitor: str) -> HTMLResponse:
    response.set_cookie("visitor", visitor, max_age=31_536_000, samesite="lax", httponly=True)
    return response


def _provider_or_none():
    """The text provider, or None when there is no key. Mirrors the bot's helper."""
    from app.llm.base import LLMError
    from app.llm.factory import get_provider

    try:
        return get_provider()
    except LLMError:
        return None


def _vision_provider_or_none():
    from app.llm.base import LLMError
    from app.llm.factory import get_vision_provider

    try:
        return get_vision_provider()
    except LLMError:
        return None


def _too_big(data: bytes) -> bool:
    return len(data) > get_settings().max_upload_mb * 1024 * 1024


def _error_message(key: str, **fmt) -> OutboundMessage:
    """A never-a-naked-error page (§8): the reason, then the four things I can do."""
    text = copy.t(key)
    if fmt:
        text = text.format(**fmt)
    return OutboundMessage.of(
        Block.para(text),
        Block.disclaimer(copy.DISCLAIMER),
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request) -> HTMLResponse:
    return _page(request, "home.html")


@router.get("/scam", response_class=HTMLResponse, name="scam_page")
async def scam_page(request: Request) -> HTMLResponse:
    return _page(request, "scam.html")


@router.post("/scam", response_class=HTMLResponse)
async def scam_submit(
    request: Request,
    message: str = Form(default=""),
    screenshot: UploadFile | None = None,
) -> HTMLResponse:
    visitor = _visitor_id(request)
    text = message.strip()
    upload = await screenshot.read() if screenshot and screenshot.filename else b""

    if not text and not upload:
        return _result(request, _error_message("scam_need_input"), feature="scam")

    if upload and _too_big(upload):
        return _with_visitor(
            _result(
                request,
                _error_message("upload_too_big", mb=get_settings().max_upload_mb),
                feature="scam",
            ),
            visitor,
        )

    with session_scope() as session:
        user = repo.get_or_create_user(session, channel=Channel.WEB, channel_user_id=visitor)
        if upload:
            provider = _vision_provider_or_none()
            if provider is None:
                out = _error_message("bad_upload")
            else:
                from app.features.scam.service import check_screenshot

                out = await check_screenshot(
                    upload, session=session, user=user, provider=provider
                )
        else:
            assessment = analyse_text(text, provider=_provider_or_none(), session=session)
            out = format_assessment(assessment, text)

    return _with_visitor(_result(request, out, feature="scam"), visitor)


@router.get("/loan", response_class=HTMLResponse, name="loan_page")
async def loan_page(request: Request) -> HTMLResponse:
    return _page(request, "loan.html")


@router.post("/loan", response_class=HTMLResponse)
async def loan_submit(request: Request, document: UploadFile | None = None) -> HTMLResponse:
    visitor = _visitor_id(request)
    data = await document.read() if document and document.filename else b""

    if not data:
        return _result(request, _error_message("loan_need_input"), feature="loan")
    if _too_big(data):
        return _with_visitor(
            _result(
                request,
                _error_message("upload_too_big", mb=get_settings().max_upload_mb),
                feature="loan",
            ),
            visitor,
        )

    provider = _vision_provider_or_none()
    with session_scope() as session:
        user = repo.get_or_create_user(session, channel=Channel.WEB, channel_user_id=visitor)
        if provider is None:
            out = _error_message("bad_upload")
        else:
            out = await check_document(
                data,
                session=session,
                user=user,
                provider=provider,
                filename=document.filename or "",
            )

    return _with_visitor(_result(request, out, feature="loan"), visitor)


@router.get("/schemes", response_class=HTMLResponse, name="schemes_page")
async def schemes_page(request: Request) -> HTMLResponse:
    return _page(
        request,
        "schemes.html",
        genders=_GENDERS,
        ages=_AGES,
        areas=_AREAS,
        incomes=_INCOMES,
        castes=_CASTES,
        needs=_NEEDS,
    )


@router.post("/schemes", response_class=HTMLResponse)
async def schemes_submit(
    request: Request,
    gender: str = Form(default=""),
    age_band: str = Form(default=""),
    state: str = Form(default=""),
    area: str = Form(default=""),
    income_band: str = Form(default=""),
    caste_category: str = Form(default=""),
    needs: list[str] = Form(default=[]),
) -> HTMLResponse:
    visitor = _visitor_id(request)
    chosen_needs = tuple(NeedCategory(n) for n in needs if n in NeedCategory._value2member_map_)

    if not chosen_needs:
        return _result(request, _error_message("schemes_need_need"), feature="schemes")

    profile = SchemeProfile(
        gender=_enum_or_none(Gender, gender),
        age_band=_enum_or_none(AgeBand, age_band),
        state=state or None,
        area=_enum_or_none(Area, area),
        income_band=_enum_or_none(IncomeBand, income_band),
        caste_category=_enum_or_none(CasteCategory, caste_category),
        needs=chosen_needs,
    )

    with session_scope() as session:
        user = repo.get_or_create_user(session, channel=Channel.WEB, channel_user_id=visitor)
        out = await find_schemes(profile, session=session, user=user, provider=_provider_or_none())

    return _with_visitor(_result(request, out, feature="schemes"), visitor)


@router.get("/budget", response_class=HTMLResponse, name="budget_page")
async def budget_page(request: Request) -> HTMLResponse:
    return _page(request, "budget.html")


@router.post("/budget", response_class=HTMLResponse)
async def budget_submit(request: Request, budget: str = Form(default="")) -> HTMLResponse:
    visitor = _visitor_id(request)
    text = budget.strip()
    if not text:
        return _result(request, _error_message("budget_need_input"), feature="budget")

    language = _lang(request)
    with session_scope() as session:
        user = repo.get_or_create_user(session, channel=Channel.WEB, channel_user_id=visitor)
        out = await check_budget_text(
            text, session=session, user=user, language=language.value
        )

    return _with_visitor(_result(request, out, feature="budget"), visitor)


@router.get("/demo", response_class=HTMLResponse, name="demo_page")
async def demo_page(request: Request) -> HTMLResponse:
    """Filled in fully in the next task; the route exists now so the nav link resolves."""
    return _page(request, "demo.html")


def _enum_or_none(enum_cls, value: str):
    """Blank ("prefer not to say") and unknown values both mean unanswered."""
    return enum_cls(value) if value in enum_cls._value2member_map_ else None
