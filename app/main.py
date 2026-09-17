"""FastAPI application — health, and the Telegram webhook in deployed mode.

In development the bot runs on its own with `python -m app.channels.telegram_bot`
and this app is not needed. In deployment there is one process: Uvicorn serves the
webhook, and later the Phase 7 dashboard, from the same port. That is deliberate —
Render and Railway both charge per service, and a demo that depends on two
processes staying up is a demo with two ways to fail.

    uvicorn app.main:app --reload

⚠ Deployment note carried forward from the build plan: Render's free tier spins a
service down when idle, which kills a *polling* bot silently. Webhook mode survives
it because the request itself wakes the service. Decide before demo day.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles

from app.channels.web_routes import router as web_router
from app.config import Settings, get_settings
from app.llm.factory import verify_configuration
from app.models import repo
from app.models.db import purge_expired_analyses
from app.models.session import init_db, session_scope

__all__ = ["app", "create_app"]

log = logging.getLogger(__name__)

VERSION = "0.1.0"
WEBHOOK_PATH = "/telegram/webhook"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()

    # Refuse to boot a provider configuration that cannot do the job. No network
    # call, so this costs nothing and works offline.
    verify_configuration(settings)

    init_db()

    with session_scope() as session:
        purged = purge_expired_analyses(session)
    if purged:
        log.info("purged %d analyses past the 90-day retention window", purged)

    application.state.telegram = None
    if settings.telegram_mode == "webhook" and settings.telegram_bot_token is not None:
        # Imported here so a deployment without a bot token — or a machine where
        # python-telegram-bot is not installed — can still serve the dashboard.
        from telegram import Update

        from app.channels.telegram_bot import build_application, webhook_secret_token

        bot_app = build_application(settings)
        await bot_app.initialize()
        await bot_app.start()
        application.state.telegram = bot_app
        application.state.telegram_update_cls = Update

        if settings.public_base_url:
            await bot_app.bot.set_webhook(
                url=f"{settings.public_base_url.rstrip('/')}{WEBHOOK_PATH}",
                secret_token=webhook_secret_token(settings),
                allowed_updates=Update.ALL_TYPES,
            )
            log.info("telegram webhook registered at %s%s", settings.public_base_url, WEBHOOK_PATH)
        else:
            log.warning(
                "TELEGRAM_MODE=webhook but PUBLIC_BASE_URL is empty — "
                "the bot will receive nothing until it is set"
            )

    try:
        yield
    finally:
        bot_app = getattr(application.state, "telegram", None)
        if bot_app is not None:
            await bot_app.stop()
            await bot_app.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title="ArthaSathi.ai",
        version=VERSION,
        description="India's financial safety & decision intelligence layer.",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, object]:
        """Liveness plus the configuration a deploy actually needs to see.

        No secret is echoed — only whether each one is present. A health endpoint
        that prints an API key is how keys end up in uptime-monitor logs.
        """
        current = get_settings()
        with session_scope() as session:
            spent = repo.spend_this_month(session)
        return {
            "status": "ok",
            "version": VERSION,
            "llm_provider": current.llm_provider,
            "vision_provider": current.vision_provider,
            "llm_model": current.gemini_model if current.llm_provider == "gemini" else None,
            "telegram_mode": current.telegram_mode,
            "telegram_token_configured": current.telegram_bot_token is not None,
            "bot_running": getattr(application.state, "telegram", None) is not None,
            "spend_this_month_inr": round(spent, 2),
            "spend_cap_inr": current.monthly_spend_cap_inr,
        }

    @application.post(WEBHOOK_PATH)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        """Hand a Telegram update to the bot.

        The secret-token header is checked first. Without it this route is a public
        endpoint that anyone can post a forged update to — including one that looks
        like it came from another user.
        """
        bot_app = getattr(request.app.state, "telegram", None)
        if bot_app is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="telegram is not running in webhook mode",
            )

        from app.channels.telegram_bot import webhook_secret_token

        if x_telegram_bot_api_secret_token != webhook_secret_token():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret token")

        update_cls = request.app.state.telegram_update_cls
        await bot_app.update_queue.put(update_cls.de_json(await request.json(), bot_app.bot))
        return {"ok": True}

    # The Phase 7 dashboard. The vendored Chart.js lives under /static so the whole
    # demo runs from a laptop with no internet. The feature pages themselves — /scam,
    # /loan, /schemes, /budget and the home page at / — come from the web router.
    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(web_router)

    return application


app = create_app()
