"""Booking risk: rules decide the score and action; a model may only explain them."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from settle.ai.llm import RULES, LLMClient
from settle.ai.prompts import load_prompt
from settle.domain.money import Money

MAX_SCORE: Final = 100


class RiskDecision(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class RiskInput:
    total: Money
    nights: int
    lead_time: timedelta  # booking time until check-in
    guest_account_age: timedelta
    guest_bookings_last_24h: int  # including this one
    guest_disputes_opened: int
    guest_declined_payments: int


@dataclass(frozen=True, slots=True)
class Signal:
    code: str
    label: str
    weight: int
    detail: str


@dataclass(frozen=True, slots=True)
class RiskResult:
    score: int
    decision: RiskDecision
    signals: list[Signal]
    explanation: str
    explained_by: str


class _Explanation(BaseModel):
    summary: str = Field(min_length=1, max_length=600)


HIGH_VALUE_MINOR: Final = 200_000  # 2,000.00 in two-decimal currencies
LONG_STAY_NIGHTS: Final = 28
VELOCITY_BOOKINGS: Final = 3


def _hours(delta: timedelta) -> int:
    return int(delta.total_seconds() // 3600)


def detect_signals(risk: RiskInput) -> list[Signal]:
    """The rules. Each is simple and explainable; together they produce the score."""
    signals: list[Signal] = []
    # A negative age means clock skew between servers; treat it as unknown, not as new.
    if timedelta(0) <= risk.guest_account_age < timedelta(days=1):
        signals.append(
            Signal(
                "new_account",
                "New account",
                20,
                f"Account created {_hours(risk.guest_account_age)} hours before booking",
            )
        )
    if risk.lead_time < timedelta(hours=48):
        signals.append(
            Signal(
                "last_minute",
                "Last-minute booking",
                15,
                f"Check-in {_hours(risk.lead_time)} hours after booking",
            )
        )
    if risk.guest_bookings_last_24h >= VELOCITY_BOOKINGS:
        signals.append(
            Signal(
                "velocity",
                "Many bookings in a day",
                25,
                f"{risk.guest_bookings_last_24h} bookings by this guest in 24 hours",
            )
        )
    if risk.total.amount >= HIGH_VALUE_MINOR:
        signals.append(Signal("high_value", "High value", 15, f"Total {risk.total}"))
    if risk.nights >= LONG_STAY_NIGHTS:
        signals.append(Signal("long_stay", "Long stay", 10, f"{risk.nights} nights"))
    if risk.guest_disputes_opened:
        signals.append(
            Signal(
                "prior_disputes",
                "Previous disputes",
                20,
                f"Guest opened {risk.guest_disputes_opened} earlier dispute(s)",
            )
        )
    if risk.guest_declined_payments:
        signals.append(
            Signal(
                "declined_payments",
                "Declined payments",
                15,
                f"{risk.guest_declined_payments} earlier payment(s) declined",
            )
        )
    return signals


class RiskAgent:
    def __init__(self, llm: LLMClient, *, review_threshold: int, hold_threshold: int) -> None:
        self._llm = llm
        self._review = review_threshold
        self._hold = hold_threshold
        self._prompt = load_prompt("risk")

    def assess(self, risk: RiskInput) -> RiskResult:
        signals = detect_signals(risk)
        score = min(MAX_SCORE, sum(signal.weight for signal in signals))
        decision = (
            RiskDecision.HOLD
            if score >= self._hold
            else RiskDecision.REVIEW
            if score >= self._review
            else RiskDecision.ALLOW
        )
        explanation, explained_by = self._explain(risk, signals, score, decision)
        return RiskResult(score, decision, signals, explanation, explained_by)

    def _explain(
        self, risk: RiskInput, signals: list[Signal], score: int, decision: RiskDecision
    ) -> tuple[str, str]:
        if signals:
            facts = {
                "score": score,
                "action": decision,
                "total": str(risk.total),
                "nights": risk.nights,
                "signals": [asdict(signal) for signal in signals],
            }
            answer = self._llm.structured(
                system=self._prompt.text, prompt=json.dumps(facts), schema=_Explanation
            )
            if answer is not None:
                return answer.summary, f"{self._llm.name}/{self._prompt.label}"
        return template_explanation(signals, score, decision), RULES


def template_explanation(signals: list[Signal], score: int, decision: RiskDecision) -> str:
    if not signals:
        return f"Score {score}: no risk signals found."
    reasons = "; ".join(f"{signal.label.lower()} ({signal.detail})" for signal in signals)
    advice = {
        RiskDecision.ALLOW: "No action needed.",
        RiskDecision.REVIEW: "Review the guest's account before check-in.",
        RiskDecision.HOLD: "Payout is held until an operator approves it.",
    }[decision]
    return f"Score {score}: {reasons}. {advice}"
