"""Runtime configuration.

Every threshold the product reasons with lives here, not scattered through feature
code. Tuning the scam blend or the spend cap is an env change, not a code change.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "deepseek", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- AI provider ---
    llm_provider: ProviderName = "mock"
    llm_vision_provider: ProviderName | None = None
    gemini_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    # Model IDs move. Confirmed against Google's docs in August 2026; overridable
    # so chasing a rename is never a code change.
    gemini_model: str = "gemini-3.6-flash"
    deepseek_model: str = "deepseek-chat"

    # --- cost accounting ---
    # Published Gemini Flash pricing, converted to rupees per million tokens. Used
    # only to decide when the monthly cap is reached; nobody is billed off this.
    gemini_input_inr_per_mtok: float = 25.0
    gemini_output_inr_per_mtok: float = 200.0

    # --- channels ---
    telegram_bot_token: SecretStr | None = None
    telegram_mode: Literal["polling", "webhook"] = "polling"
    public_base_url: str | None = None

    # --- storage ---
    database_url: str = "sqlite:///./data/arthasathi.db"

    # --- scam detection ---
    scam_high_risk_threshold: int = 51
    scam_rule_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    scam_disagreement_downgrade: int = 40
    scam_min_rules_above_50: int = 2

    # --- cost + abuse control ---
    max_upload_mb: int = 10
    image_max_edge_px: int = 1568
    # Below this many characters per page a PDF is a scan, and only a vision model
    # can read it. A digital page yields hundreds; a scanned one yields single digits.
    pdf_min_chars_per_page: int = 120
    # GST on financial service fees. A named setting because it is a tax rate, not
    # a constant, and a feature file should not have to know it.
    gst_rate_pct: float = 18.0
    daily_analyses_per_user: int = 20
    monthly_spend_cap_inr: int = 2000

    @field_validator(
        "llm_vision_provider",
        "gemini_api_key",
        "deepseek_api_key",
        "telegram_bot_token",
        "public_base_url",
        mode="before",
    )
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        """An empty env var means "not set", not "set to nothing".

        `.env.example` ships these keys with nothing after the `=`. Without this, a
        copied .env gives a `SecretStr('')` that passes every `is not None` check
        and then fails deep inside an SDK with an unreadable authentication error.
        """
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def vision_provider(self) -> ProviderName:
        """Provider used for images and scanned PDFs.

        Falls back to the main provider. A text-only provider selected here is
        rejected at startup by app.llm.factory, not mid-analysis in front of a user.
        """
        return self.llm_vision_provider or self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
