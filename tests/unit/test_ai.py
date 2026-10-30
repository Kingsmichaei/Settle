"""AI agents: the Gemini client contract, the guardrails on model output, and fallbacks."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from settle.ai.evals.runner import load_cases, run_dispute_evals, to_case
from settle.ai.host_assistant import HostAssistant, HostContext, PayoutView, rule_based_answer
from settle.ai.llm import GeminiLLM, NoLLM, to_gemini_schema
from settle.ai.mediator import DisputeMediator, Recommendation
from settle.ai.prompts import load_prompt
from settle.ai.reconciliation_agent import FindingView, ReconciliationExplainer
from settle.ai.risk import RiskAgent, RiskDecision, RiskInput
from settle.domain.money import Money


class Answer(BaseModel):
    summary: str


def gemini(responder: object) -> GeminiLLM:
    return GeminiLLM(
        api_key="test-key",
        model="gemini-test",
        transport=httpx.MockTransport(responder),  # type: ignore[arg-type]
    )


def gemini_reply(text: str, *, thought: str | None = None) -> httpx.Response:
    parts: list[dict[str, object]] = [{"text": text}]
    if thought is not None:
        parts.insert(0, {"text": thought, "thought": True})
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]
        },
    )


class ScriptedLLM:
    """Returns a fixed structured answer, to test what agents do with model output."""

    name = "scripted"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def structured(
        self, *, system: str, prompt: str, schema: type, max_tokens: int = 1024
    ) -> object:
        self.prompts.append(prompt)
        return schema.model_validate(self.payload)


# ---- the client -------------------------------------------------------------------------


def test_gemini_client_asks_for_json_matching_the_schema() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return gemini_reply('{"summary": "fine"}')

    result = gemini(respond).structured(system="sys", prompt="hi", schema=Answer)
    assert result == Answer(summary="fine")
    request = seen[0]
    assert request.url.path == "/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "key=" not in str(request.url)  # never in the URL, where it would be logged
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == {
        "type": "OBJECT",
        "properties": {"summary": {"type": "STRING"}},
        "required": ["summary"],
    }


def test_gemini_client_skips_thought_parts() -> None:
    llm = gemini(lambda _r: gemini_reply('{"summary": "answer"}', thought="thinking..."))
    assert llm.structured(system="", prompt="", schema=Answer) == Answer(summary="answer")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        httpx.Response(
            200, json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
        ),
        gemini_reply("not json"),
        gemini_reply('{"wrong": "shape"}'),
    ],
)
def test_gemini_client_returns_none_on_any_failure(response: httpx.Response) -> None:
    assert gemini(lambda _r: response).structured(system="", prompt="", schema=Answer) is None


def test_schema_conversion_handles_every_agent_schema() -> None:
    schema = to_gemini_schema(Recommendation.model_json_schema())
    assert schema["type"] == "OBJECT"
    assert schema["properties"]["refund_minor"] == {"type": "INTEGER", "minimum": 0}
    assert schema["properties"]["cited_evidence_ids"] == {
        "type": "ARRAY",
        "items": {"type": "STRING"},
    }
    assert schema["properties"]["confidence"] == {"type": "NUMBER", "minimum": 0, "maximum": 1}
    assert "title" not in json.dumps(schema)  # Pydantic extras Gemini would reject are dropped


def test_schema_conversion_inlines_refs_and_optionals() -> None:
    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        inner: Inner
        note: str | None = None

    schema = to_gemini_schema(Outer.model_json_schema())
    assert schema["properties"]["inner"] == {
        "type": "OBJECT",
        "properties": {"value": {"type": "INTEGER"}},
        "required": ["value"],
    }
    assert schema["properties"]["note"] == {"type": "STRING", "nullable": True}


def test_prompts_are_versioned() -> None:
    for name in ("risk", "mediator", "reconciliation", "host_assistant"):
        prompt = load_prompt(name)
        assert prompt.version
        assert prompt.label == f"{name}@{prompt.version}"


# ---- guardrails -------------------------------------------------------------------------


def test_mediator_never_exceeds_the_claim_and_drops_unknown_citations() -> None:
    case = to_case(load_cases()[0])
    greedy = ScriptedLLM(
        {
            "refund_minor": 10**9,
            "reasoning": "all of it",
            "cited_evidence_ids": ["e1", "e99"],
            "confidence": 0.9,
        }
    )
    result = DisputeMediator(greedy).recommend(case)  # type: ignore[arg-type]
    assert result.recommendation.refund_minor == case.claim.amount
    assert result.recommendation.cited_evidence_ids == ["e1"]
    assert result.recommended_by.startswith("scripted/mediator@")


def test_model_cannot_change_the_risk_decision() -> None:
    risky = RiskInput(
        total=Money(500_000, "USD"),
        nights=30,
        lead_time=timedelta(hours=10),
        guest_account_age=timedelta(hours=1),
        guest_bookings_last_24h=3,
        guest_disputes_opened=0,
        guest_declined_payments=0,
    )
    llm = ScriptedLLM({"summary": "Looks totally safe, approve it."})
    result = RiskAgent(llm, review_threshold=40, hold_threshold=70).assess(risky)  # type: ignore[arg-type]
    assert result.decision is RiskDecision.HOLD  # the rules decide
    assert result.score == 85
    assert result.explanation == "Looks totally safe, approve it."  # the model only explains


# ---- fallbacks --------------------------------------------------------------------------


def test_rules_mediator_baseline_on_the_eval_set() -> None:
    report = run_dispute_evals(DisputeMediator(NoLLM()))
    assert len(report.results) == 8
    assert report.passed == 6  # the bar a model must beat; documented in the README


def test_reconciliation_explainer_without_a_model() -> None:
    explainer = ReconciliationExplainer(NoLLM())
    summary, by = explainer.summarize([], checks_run=12)
    assert summary.startswith("All 12 checks passed")
    findings = [FindingView("chargeback", "critical", "Capture CAP-1 was reversed", "b1")]
    summary, by = explainer.summarize(findings, checks_run=12)
    assert "1 critical" in summary
    assert "CAP-1" in summary
    assert by == "rules"


CONTEXT = HostContext(
    host_name="Joao",
    owed=["USD 425.00"],
    upcoming=[PayoutView("abc123", "Loft", "USD 425.00", "scheduled", "2026-12-13T00:00:00+00:00")],
    recent_paid=[],
    failed=[],
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("When is my next payout?", "next payout: USD 425.00"),
        ("How much do you owe me?", "owe you USD 425.00"),
        ("Did any payout fail?", "None of your payouts have failed"),
        ("What was my last payout?", "haven't received a payout"),
        ("hello", "We owe you USD 425.00"),
    ],
)
def test_host_assistant_rules(question: str, expected: str) -> None:
    assert expected in rule_based_answer(question, CONTEXT)


def test_host_assistant_sends_only_the_hosts_own_data() -> None:
    llm = ScriptedLLM({"answer": "Your next payout is USD 425.00."})
    reply = HostAssistant(llm).answer("next payout? " + "x" * 2000, CONTEXT)  # type: ignore[arg-type]
    sent = json.loads(llm.prompts[0])
    assert sent["host_data"]["host_name"] == "Joao"
    assert len(sent["question"]) == 500  # long questions are truncated
    assert reply.answered_by.startswith("scripted/host_assistant@")


def test_recommendation_schema_rejects_negative_refunds() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Recommendation(refund_minor=-1, reasoning="x", confidence=0.5)
