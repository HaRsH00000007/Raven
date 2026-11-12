from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Raven"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://raven:raven@localhost:5432/raven"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    admin_token: str = "change-me-admin"
    # When set, startup creates (or reuses) a workspace that authenticates with this key.
    dev_bootstrap_api_key: str | None = None
    dev_bootstrap_workspace_name: str = "Demo Workspace"
    dev_bootstrap_credits: int = 5000

    # Reply routing
    reply_domain: str = "replies.example.com"
    inbound_webhook_token: str = "change-me-webhook"
    twilio_auth_token: str | None = None
    public_base_url: str = "http://localhost:8000"

    # Forwarding positives by email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Raven <notifications@example.com>"
    smtp_starttls: bool = True

    # Credits
    credit_validity_days: int = 60  # 1 billing month + 1 rollover month

    # Voicemail transcription (optional; first configured wins, else the provided/placeholder text)
    deepgram_api_key: str | None = None
    openai_api_key: str | None = None
    whisper_model: str = "whisper-1"
    twilio_account_sid: str | None = None  # for downloading protected recordings

    # Local simulator endpoint (/simulate/inbound). Always on outside production.
    allow_simulator: bool = False

    # Outbound CRM webhooks
    webhook_timeout_seconds: float = 10.0

    # LLM (optional: rules-based fallback is always available)
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_timeout_seconds: float = 30.0

    # Form engine
    screenshot_dir: Path = Path("./data/screenshots")
    cooldown_days: int = 90
    bill_unverified: bool = False
    worker_concurrency: int = 8
    page_timeout_ms: int = 25000
    submit_wait_ms: int = 12000
    bot_user_agent_token: str = "RavenFormBot/1.0"
    bot_info_url: str = "https://example.com/bot"
    default_price_per_form_cents: int = 6

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
