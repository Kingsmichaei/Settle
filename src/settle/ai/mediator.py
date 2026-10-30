"""Dispute mediator: recommends a refund. An operator always makes the decision."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final, Literal

from pydantic import BaseModel, Field

from settle.ai.llm import RULES, LLMClient
from settle.ai.prompts import load_prompt
from settle.domain.money import Money

Party = Literal["guest", "host"]
EvidenceType = Literal["statement", "photo"]

EVIDENCE_WEIGHT: Final[dict[str, int]] = {"photo": 2, "statement": 1}


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: str
    party: Party
    kind: EvidenceType
    content: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class DisputeCase:
    """A guest's refund claim. The host responds with evidence of their own."""

    reason: str
    claim: Money
    max_refund: Money
    nights: int
    total: Money
    evidence: tuple[EvidenceItem, ...]


class Recommendation(BaseModel):
    refund_minor: int = Field(ge=0)
    reasoning: str = Field(min_length=1, max_length=1500)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True, slots=True)
class MediationResult:
    recommendation: Recommendation
    recommended_by: str


class DisputeMediator:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("mediator")

    def recommend(self, case: DisputeCase) -> MediationResult:
        answer = self._llm.structured(
            system=self._prompt.text, prompt=_describe(case), schema=Recommendation, max_tokens=1500
        )
        if answer is not None:
            return MediationResult(
                _enforce_limits(answer, case), f"{self._llm.name}/{self._prompt.label}"
            )
        return MediationResult(rule_based_recommendation(case), RULES)


def refund_cap(case: DisputeCase) -> Money:
    """A refund can never exceed what was claimed or what is still refundable."""
    return min(case.claim, case.max_refund)


def _enforce_limits(answer: Recommendation, case: DisputeCase) -> Recommendation:
    known = {item.id for item in case.evidence}
    return answer.model_copy(
        update={
            "refund_minor": min(answer.refund_minor, refund_cap(case).amount),
            "cited_evidence_ids": [ref for ref in answer.cited_evidence_ids if ref in known],
        }
    )


def rule_based_recommendation(case: DisputeCase) -> Recommendation:
    """Weigh each side's evidence (a photo counts double a statement) and split the claim.

    Deliberately simple and transparent; it is the floor the model has to beat in the evals.
    """
    cap = refund_cap(case)
    claimant_weight = sum(
        EVIDENCE_WEIGHT[item.kind] for item in case.evidence if item.party == "guest"
    )
    respondent_weight = sum(
        EVIDENCE_WEIGHT[item.kind] for item in case.evidence if item.party == "host"
    )
    cited = [item.id for item in case.evidence]

    if respondent_weight == 0 and claimant_weight > 0:
        return Recommendation(
            refund_minor=cap.amount,
            reasoning="The guest's claim is supported by evidence and the host has not responded.",
            cited_evidence_ids=cited,
            confidence=0.5,
        )
    if claimant_weight == 0:
        return Recommendation(
            refund_minor=0,
            reasoning="The guest has not provided any evidence to support the claim.",
            cited_evidence_ids=cited,
            confidence=0.5,
        )
    share = Decimal(claimant_weight) / (claimant_weight + respondent_weight)
    refund = int((Decimal(cap.amount) * share).quantize(Decimal(1), ROUND_HALF_EVEN))
    return Recommendation(
        refund_minor=refund,
        reasoning=(
            f"Both sides provided evidence; the guest's evidence carries {share:.0%} of the "
            "weight, so the refund is that share of the claim."
        ),
        cited_evidence_ids=cited,
        confidence=0.3,
    )


def _describe(case: DisputeCase) -> str:
    return json.dumps(
        {
            "claimant": "guest",
            "reason": case.reason,
            "claim_minor": case.claim.amount,
            "max_refundable_minor": refund_cap(case).amount,
            "currency": case.claim.currency,
            "booking": {"nights": case.nights, "total": str(case.total)},
            "evidence": [
                {
                    "id": item.id,
                    "party": item.party,
                    "kind": item.kind,
                    "content": item.content,
                    "url": item.url,
                }
                for item in case.evidence
            ],
        },
        indent=2,
    )
