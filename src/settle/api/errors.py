"""Map domain errors to consistent problem-details responses."""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from settle.api.schemas import ProblemOut
from settle.core import errors
from settle.providers.base import TransientProviderError

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[errors.SettleError], HTTPStatus] = {
    errors.ValidationFailed: HTTPStatus.UNPROCESSABLE_ENTITY,
    errors.CurrencyMismatch: HTTPStatus.UNPROCESSABLE_ENTITY,
    errors.IdempotencyKeyReused: HTTPStatus.UNPROCESSABLE_ENTITY,
    errors.NotFound: HTTPStatus.NOT_FOUND,
    errors.InvalidTransition: HTTPStatus.CONFLICT,
    errors.RequestInProgress: HTTPStatus.CONFLICT,
    errors.Unauthenticated: HTTPStatus.UNAUTHORIZED,
    errors.Forbidden: HTTPStatus.FORBIDDEN,
    errors.UnbalancedEntry: HTTPStatus.INTERNAL_SERVER_ERROR,
}


def problem(status: HTTPStatus, code: str, detail: str) -> JSONResponse:
    body = ProblemOut(type=code, title=status.phrase, status=status.value, detail=detail)
    headers = {"WWW-Authenticate": "Bearer"} if status is HTTPStatus.UNAUTHORIZED else None
    return JSONResponse(
        body.model_dump(), status.value, media_type="application/problem+json", headers=headers
    )


def _status_for(exc: Exception) -> HTTPStatus:
    for error_type, status in _STATUS_BY_ERROR.items():
        if isinstance(exc, error_type):
            return status
    return HTTPStatus.BAD_REQUEST


async def _handle_settle_error(_request: Request, exc: Exception) -> JSONResponse:
    status = _status_for(exc)
    code = exc.code if isinstance(exc, errors.SettleError) else "error"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        logger.error("Internal invariant violated: %s", exc)
    return problem(status, code, str(exc))


async def _handle_stale_data(_request: Request, _exc: Exception) -> JSONResponse:
    return problem(
        HTTPStatus.CONFLICT,
        "concurrent_update",
        "The booking was changed by another request. Fetch it again and retry.",
    )


async def _handle_provider_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    logger.warning("Payment provider unavailable: %s", exc)
    return problem(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "provider_unavailable",
        "The payment provider is not responding. Please try again in a moment.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(errors.SettleError, _handle_settle_error)
    app.add_exception_handler(StaleDataError, _handle_stale_data)
    app.add_exception_handler(TransientProviderError, _handle_provider_unavailable)
