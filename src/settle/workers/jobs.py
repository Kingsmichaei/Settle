"""Scheduled jobs, shared by the standalone worker and the embedded (in-API) worker."""

from __future__ import annotations

import logging

from apscheduler.schedulers.base import BaseScheduler

from settle.bootstrap import Container
from settle.db.session import transaction
from settle.services.payouts import release_due_payouts
from settle.services.reconciliation import run_reconciliation

logger = logging.getLogger(__name__)


def relay_outbox(container: Container) -> None:
    report = container.relay.run_once()
    if report.processed:
        logger.info(
            "Outbox: %d done, %d retrying, %d dead", report.succeeded, report.retried, report.dead
        )


def release_payouts(container: Container) -> None:
    with transaction(container.session_factory) as session:
        released = release_due_payouts(session, container.clock)
    if released:
        logger.info("Released %d payouts", released)


def reconcile(container: Container) -> None:
    with transaction(container.session_factory) as session:
        run = run_reconciliation(session, container.services)
    logger.info("Reconciliation: %d checks, %d findings", run.checks_run, len(run.findings))


def register_jobs(scheduler: BaseScheduler, container: Container) -> None:
    settings = container.settings
    # max_instances=1 + coalesce: a slow run is never overlapped by the next one.
    jobs = (
        (relay_outbox, settings.worker_poll_seconds, "relay-outbox"),
        (release_payouts, settings.payout_release_interval_seconds, "release-payouts"),
        (reconcile, settings.reconciliation_interval_seconds, "reconcile"),
    )
    for job, seconds, job_id in jobs:
        scheduler.add_job(
            job,
            "interval",
            seconds=seconds,
            args=[container],
            id=job_id,
            max_instances=1,
            coalesce=True,
        )
