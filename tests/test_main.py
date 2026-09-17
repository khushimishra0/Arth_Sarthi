"""FastAPI app tests. The app boots, reports honestly, and leaks nothing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import WEBHOOK_PATH, app


@pytest.fixture
def client() -> TestClient:
    """Runs the real lifespan — so this also proves init_db works on a cold start."""
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"
    assert body["telegram_mode"] == "polling"


def test_health_reports_whether_the_bot_is_configured_without_printing_the_token(client):
    """A health endpoint that echoes a key is how keys reach uptime-monitor logs."""
    body = client.get("/health").json()
    assert body["telegram_token_configured"] is False
    assert body["bot_running"] is False

    # Presence is reported as a boolean. Nothing that could hold a credential is a
    # string in this payload.
    for name, value in body.items():
        if any(word in name for word in ("token", "key", "secret", "password")):
            assert isinstance(value, bool), f"{name} must be a boolean, not the value itself"


def test_the_home_page_is_the_dashboard_with_all_four_features(client):
    """Phase 7: `/` is the web dashboard now, not a JSON pointer at Telegram."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    for link in ("/scam", "/loan", "/schemes", "/budget"):
        assert link in body


def test_the_webhook_is_closed_when_the_bot_is_not_running(client):
    """Polling mode in development. The route must refuse, not crash."""
    response = client.post(WEBHOOK_PATH, json={"update_id": 1})
    assert response.status_code == 503


def test_an_empty_env_var_means_unset_rather_than_set_to_nothing():
    """`.env.example` ships blank keys; a copied .env must not look configured."""
    settings = Settings(telegram_bot_token="", gemini_api_key="   ", public_base_url="")
    assert settings.telegram_bot_token is None
    assert settings.gemini_api_key is None
    assert settings.public_base_url is None


def test_the_vision_provider_defaults_to_the_main_provider():
    assert Settings(llm_provider="gemini").vision_provider == "gemini"
    split = Settings(llm_provider="deepseek", llm_vision_provider="gemini")
    assert split.vision_provider == "gemini"
