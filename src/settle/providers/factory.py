"""Builds the configured provider."""

from __future__ import annotations

from settle.config import Settings
from settle.providers.base import PaymentProvider
from settle.providers.fake import FakeProvider
from settle.providers.paypal import PayPalCredentials, PayPalProvider


def build_provider(settings: Settings) -> PaymentProvider:
    match settings.payment_provider:
        case "fake":
            return FakeProvider(
                webhook_secret=settings.fake_webhook_secret.get_secret_value(),
                approval_base_url=f"{settings.public_base_url.rstrip('/')}/sandbox/approve",
                async_payouts=settings.fake_async_payouts,
            )
        case "paypal":
            return PayPalProvider(
                PayPalCredentials(
                    client_id=settings.paypal_client_id,
                    client_secret=settings.paypal_client_secret.get_secret_value(),
                    webhook_id=settings.paypal_webhook_id,
                ),
                base_url=settings.paypal_base_url,
                timeout_seconds=settings.paypal_timeout_seconds,
            )
