"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from settle.api.deps import ContainerDep, SessionDep
from settle.api.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(session: SessionDep, container: ContainerDep) -> HealthOut:
    session.execute(text("SELECT 1"))
    return HealthOut(
        status="ok",
        database="ok",
        provider=container.provider.name,
        ai=container.services.llm.name,
    )
