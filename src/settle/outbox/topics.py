"""Names of the commands that flow through the outbox."""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    AUTHORIZE_PAYMENT = "payment.authorize"
    CAPTURE_PAYMENT = "payment.capture"
    VOID_PAYMENT = "payment.void"
    REFUND_PAYMENT = "payment.refund"
    SEND_PAYOUT = "payout.send"
    PROCESS_WEBHOOK = "webhook.process"
    RECOMMEND_DISPUTE = "dispute.recommend"
    RUN_RECONCILIATION = "reconciliation.run"
