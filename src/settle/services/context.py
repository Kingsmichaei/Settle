"""The collaborators services need, bundled so call sites stay short and wiring stays central."""

from __future__ import annotations

from dataclasses import dataclass

from settle.ai.host_assistant import HostAssistant
from settle.ai.llm import LLMClient
from settle.ai.mediator import DisputeMediator
from settle.ai.reconciliation_agent import ReconciliationExplainer
from settle.ai.risk import RiskAgent
from settle.config import Settings
from settle.core.clock import Clock
from settle.domain.fx import FxRates
from settle.providers.base import PaymentProvider


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    clock: Clock
    provider: PaymentProvider
    fx: FxRates
    llm: LLMClient
    risk: RiskAgent
    mediator: DisputeMediator
    explainer: ReconciliationExplainer
    assistant: HostAssistant

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        provider: PaymentProvider,
        fx: FxRates,
        llm: LLMClient,
    ) -> Services:
        return cls(
            settings=settings,
            clock=clock,
            provider=provider,
            fx=fx,
            llm=llm,
            risk=RiskAgent(
                llm,
                review_threshold=settings.risk_review_threshold,
                hold_threshold=settings.risk_hold_threshold,
            ),
            mediator=DisputeMediator(llm),
            explainer=ReconciliationExplainer(llm),
            assistant=HostAssistant(llm),
        )
