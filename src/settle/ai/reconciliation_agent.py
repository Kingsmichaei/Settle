"""Turns reconciliation findings into a summary an on-call engineer can act on."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field

from settle.ai.llm import RULES, LLMClient
from settle.ai.prompts import load_prompt

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True, slots=True)
class FindingView:
    code: str
    severity: str
    message: str
    booking_id: str | None


class _Summary(BaseModel):
    summary: str = Field(min_length=1, max_length=1500)


class ReconciliationExplainer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("reconciliation")

    def summarize(self, findings: Sequence[FindingView], checks_run: int) -> tuple[str, str]:
        if not findings:
            return f"All {checks_run} checks passed. The ledger matches every record.", RULES
        payload = {"checks_run": checks_run, "findings": [asdict(item) for item in findings]}
        answer = self._llm.structured(
            system=self._prompt.text, prompt=json.dumps(payload), schema=_Summary
        )
        if answer is not None:
            return answer.summary, f"{self._llm.name}/{self._prompt.label}"
        return template_summary(findings, checks_run), RULES


def template_summary(findings: Sequence[FindingView], checks_run: int) -> str:
    ordered = sorted(findings, key=lambda item: SEVERITY_ORDER.get(item.severity, 9))
    counts = Counter(item.severity for item in findings)
    head = ", ".join(
        f"{counts[level]} {level}" for level in ("critical", "warning", "info") if counts[level]
    )
    lines = [f"{len(findings)} finding(s) across {checks_run} checks: {head}."]
    lines += [f"[{item.severity}] {item.message}" for item in ordered[:5]]
    if len(ordered) > 5:
        lines.append(f"...and {len(ordered) - 5} more.")
    return " ".join(lines)
