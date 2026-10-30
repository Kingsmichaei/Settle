"""Application settings, read from ``SETTLE_*`` environment variables or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SETTLE_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # ---- storage ------------------------------------------------------------------------
    # SQLite for local development, Postgres in production. Only the URL changes.
    database_url: str = "sqlite:///./settle.db"

    # ---- web ----------------------------------------------------------------------------
    # Where the frontend lives; used for PayPal return links and CORS.
    public_base_url: str = "http://localhost:5173"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Serve the built React app from the API when it exists (relative to the working dir).
    serve_frontend: bool = True
    frontend_dist: Path = Path("web/dist")

    # ---- auth ---------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_ttl_minutes: int = Field(default=12 * 60, gt=0)

    # ---- payments -----------------------------------------------------------------------
    payment_provider: Literal["fake", "paypal"] = "fake"
    platform_fee_bps: int = Field(default=1500, ge=0, lt=10_000)
    # Host is paid this many hours after check-in starts (00:00 UTC on the check-in date).
    payout_hold_hours: int = Field(default=24, ge=0)
    # Units of the target currency per unit of the source, e.g. {"USD:EUR": "0.92"}.
    fx_rates: dict[str, str] = Field(
        default_factory=lambda: {"USD:EUR": "0.92", "USD:GBP": "0.79", "USD:NGN": "1550"}
    )

    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_client_id: str = ""
    paypal_client_secret: SecretStr = SecretStr("")
    paypal_webhook_id: str = ""
    paypal_timeout_seconds: float = Field(default=20.0, gt=0)

    # Shared secret the fake provider signs its webhooks with.
    fake_webhook_secret: SecretStr = SecretStr("fake-webhook-secret")
    # Fake payouts complete immediately unless this is set (then a webhook confirms them).
    fake_async_payouts: bool = False

    # ---- worker -------------------------------------------------------------------------
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    payout_release_interval_seconds: float = Field(default=60.0, gt=0)
    reconciliation_interval_seconds: float = Field(default=6 * 60 * 60.0, gt=0)
    outbox_batch_size: int = Field(default=50, gt=0)
    outbox_max_attempts: int = Field(default=8, gt=0)
    # Run the worker inside the API process. Handy for a single-terminal dev setup.
    embedded_worker: bool = False

    # How long an in-progress idempotency key blocks retries before it may be re-run.
    idempotency_lock_seconds: int = Field(default=60, gt=0)

    # ---- AI -----------------------------------------------------------------------------
    # "none" runs every agent on its deterministic fallback, so the app works with no key.
    ai_provider: Literal["none", "gemini"] = "none"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    # Cheapest current model with a free tier; any Gemini model id works here.
    ai_model: str = "gemini-3.5-flash-lite"
    ai_timeout_seconds: float = Field(default=45.0, gt=0)

    # Risk score (0-100) at or above which a booking is reviewed, or its payout held.
    risk_review_threshold: int = Field(default=40, ge=0, le=100)
    risk_hold_threshold: int = Field(default=70, ge=0, le=100)

    @model_validator(mode="after")
    def _check_production_safety(self) -> Self:
        if self.environment == "production":
            if self.jwt_secret.get_secret_value() == DEV_JWT_SECRET:
                raise ValueError("Set SETTLE_JWT_SECRET to a long random value in production")
            if self.payment_provider == "fake":
                raise ValueError("The fake payment provider cannot be used in production")
        if self.payment_provider == "paypal" and not (
            self.paypal_client_id and self.paypal_client_secret.get_secret_value()
        ):
            raise ValueError("SETTLE_PAYPAL_CLIENT_ID and SETTLE_PAYPAL_CLIENT_SECRET are required")
        if self.ai_provider == "gemini" and not self.gemini_api_key.get_secret_value():
            raise ValueError("SETTLE_GEMINI_API_KEY is required when SETTLE_AI_PROVIDER=gemini")
        if self.risk_review_threshold > self.risk_hold_threshold:
            raise ValueError("risk_review_threshold must not exceed risk_hold_threshold")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
