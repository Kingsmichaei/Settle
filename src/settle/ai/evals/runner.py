"""Score the dispute mediator against hand-labelled cases: ``settle eval-disputes``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

from settle.ai.mediator import DisputeCase, DisputeMediator, EvidenceItem
from settle.domain.money import Money

CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    recommended_minor: int
    expected_min: int
    expected_max: int
    recommended_by: str

    @property
    def passed(self) -> bool:
        return self.expected_min <= self.recommended_minor <= self.expected_max


@dataclass(frozen=True, slots=True)
class EvalReport:
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def accuracy(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0


def load_cases() -> list[dict[str, Any]]:
    raw = resources.files("settle.ai").joinpath("evals", "disputes.json").read_text("utf-8")
    cases: list[dict[str, Any]] = json.loads(raw)
    return cases


def to_case(raw: dict[str, Any]) -> DisputeCase:
    claim = Money(int(raw["claim_minor"]), CURRENCY)
    total = Money(int(raw["total_minor"]), CURRENCY)
    evidence = tuple(
        EvidenceItem(
            id=f"e{index}",
            party=item["party"],
            kind=item["kind"],
            content=item["content"],
            url=item.get("url"),
        )
        for index, item in enumerate(raw["evidence"], start=1)
    )
    return DisputeCase(
        reason=raw["reason"],
        claim=claim,
        max_refund=total,
        nights=int(raw["nights"]),
        total=total,
        evidence=evidence,
    )


def run_dispute_evals(mediator: DisputeMediator) -> EvalReport:
    results = []
    for raw in load_cases():
        outcome = mediator.recommend(to_case(raw))
        results.append(
            CaseResult(
                name=raw["name"],
                recommended_minor=outcome.recommendation.refund_minor,
                expected_min=int(raw["expected_min"]),
                expected_max=int(raw["expected_max"]),
                recommended_by=outcome.recommended_by,
            )
        )
    return EvalReport(results)
