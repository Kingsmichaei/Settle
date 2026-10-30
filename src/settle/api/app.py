"""FastAPI application factory. Run with ``uvicorn settle.api.app:create_app --factory``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from settle import __version__
from settle.api.errors import register_error_handlers
from settle.api.routers import admin, auth, bookings, disputes, health, hosts, listings, webhooks
from settle.bootstrap import Container, build_container
from settle.config import get_settings
from settle.workers.jobs import register_jobs

logger = logging.getLogger(__name__)

ROUTERS = (
    health.router,
    auth.router,
    listings.router,
    bookings.router,
    disputes.router,
    hosts.router,
    admin.router,
    webhooks.router,
)


def create_app(container: Container | None = None) -> FastAPI:
    owns_container = container is None
    resolved = container or build_container(get_settings())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        scheduler: BackgroundScheduler | None = None
        if resolved.settings.embedded_worker:
            scheduler = BackgroundScheduler(timezone="UTC")
            register_jobs(scheduler, resolved)
            scheduler.start()
            logger.info("Embedded worker started")
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=True)
            if owns_container:
                resolved.close()

    app = FastAPI(
        title="Settle",
        version=__version__,
        summary="Guest-to-host payments engine for rental marketplaces.",
        lifespan=lifespan,
    )
    app.state.container = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["Idempotent-Replayed"],
    )
    register_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
    dist = resolved.settings.frontend_dist.resolve()
    if resolved.settings.serve_frontend and (dist / "index.html").exists():
        _mount_frontend(app, dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built React app, falling back to index.html for client-side routes."""
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, _request: Request) -> Response:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index)
