"""Answers a host's questions about their own earnings, from their own records only."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field

from settle.ai.llm import RULES, LLMClient
from settle.ai.prompts import load_prompt

MAX_QUESTION_LENGTH: Final = 500


@dataclass(frozen=True, slots=True)
class PayoutView:
    booking_ref: str  # short booking id the host can recognise
    listing: str
    amount: str  # formatted, e.g. "USD 425.00"
    status: str
    release_at: str  # ISO date-time
    host_receives: str | None = None  # e.g. "EUR 391.00" once converted
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class HostContext:
    host_name: str
    owed: list[str]  # formatted balances per currency
    upcoming: list[PayoutView]  # scheduled, blocked, queued or sent; soonest first
    recent_paid: list[PayoutView]  # newest first
    failed: list[PayoutView]


class _Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=1500)


@dataclass(frozen=True, slots=True)
class AssistantReply:
    answer: str
    answered_by: str


class HostAssistant:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("host_assistant")

    def answer(self, question: str, context: HostContext) -> AssistantReply:
        question = question.strip()[:MAX_QUESTION_LENGTH]
        prompt = json.dumps({"host_data": asdict(context), "question": question}, indent=2)
        reply = self._llm.structured(system=self._prompt.text, prompt=prompt, schema=_Answer)
        if reply is not None:
            return AssistantReply(reply.answer, f"{self._llm.name}/{self._prompt.label}")
        return AssistantReply(rule_based_answer(question, context), RULES)


def _day(iso: str) -> str:
    moment = datetime.fromisoformat(iso)
    return f"{moment.day} {moment:%b %Y}"  # portable: no platform-specific %-d


def _describe(payout: PayoutView) -> str:
    received = f" (you receive {payout.host_receives})" if payout.host_receives else ""
    return (
        f"{payout.amount}{received} for booking {payout.booking_ref} at {payout.listing}, "
        f"{payout.status}, released {_day(payout.release_at)}"
    )


def _failures(context: HostContext) -> str:
    if not context.failed:
        return "None of your payouts have failed."
    details = "; ".join(
        f"{_describe(p)}: {p.failure_reason or 'no reason given'}" for p in context.failed
    )
    return f"Failed payouts: {details}. Update your payout details, then ask support to retry."


def _next_payout(context: HostContext) -> str:
    if not context.upcoming:
        return "You have no upcoming payouts right now."
    return f"Your next payout: {_describe(context.upcoming[0])}."


def _last_payout(context: HostContext) -> str:
    if not context.recent_paid:
        return "You haven't received a payout yet."
    return f"Your most recent payout: {_describe(context.recent_paid[0])}."


def _balance(context: HostContext) -> str:
    owed = ", ".join(context.owed) if context.owed else "nothing"
    return f"We currently owe you {owed}, paid out 24 hours after each guest checks in."


def _overview(context: HostContext) -> str:
    owed = ", ".join(context.owed) if context.owed else "nothing"
    return (
        f"We owe you {owed}. You have {len(context.upcoming)} upcoming payout(s) and "
        f"{len(context.failed)} failed. Ask about your next payout, recent payouts or failures."
    )


# Checked in order: the first intent whose keywords appear in the question answers it.
_INTENTS: Final[tuple[tuple[tuple[str, ...], Callable[[HostContext], str]], ...]] = (
    (("fail", "problem", "wrong", "missing"), _failures),
    (("next", "when", "upcoming", "soon", "schedule"), _next_payout),
    (("last", "recent", "received", "paid"), _last_payout),
    (("owe", "balance", "how much", "total", "earn"), _balance),
)


def rule_based_answer(question: str, context: HostContext) -> str:
    """Keyword routing over the host's data. Predictable, and never makes anything up."""
    asked = question.lower()
    for keywords, respond in _INTENTS:
        if any(word in asked for word in keywords):
            return respond(context)
    return _overview(context)
