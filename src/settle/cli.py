"""Command-line entry point.

settle migrate         apply database migrations
settle seed            create demo accounts and listings
settle demo            run the full story end to end on a throwaway database
settle reconcile       run every reconciliation check now
settle eval-disputes   score the dispute mediator against labelled cases
"""

from __future__ import annotations

import argparse
import logging

from alembic import command
from alembic.config import Config

from settle.ai.evals.runner import run_dispute_evals
from settle.bootstrap import build_container
from settle.config import Settings, get_settings
from settle.db.session import transaction
from settle.demo import run_demo
from settle.seed import DEMO_PASSWORD, seed_demo_data
from settle.services.reconciliation import run_reconciliation


def migrate(database_url: str) -> None:
    """Apply all Alembic migrations to ``database_url``.

    The migrations ship inside the package, so this works from any install, not only a
    checkout of the repository.
    """
    config = Config()
    config.set_main_option("script_location", "settle:migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def seed(settings: Settings) -> None:
    container = build_container(settings)
    with transaction(container.session_factory) as session:
        data = seed_demo_data(session)
        print(f"Demo accounts (password: {DEMO_PASSWORD})")
        for user in (data.guest, data.host, data.host_ng, data.admin):
            print(f"  {user.role.value:<6} {user.email}")
        print("Listings")
        for listing in data.listings:
            print(
                f"  {listing.title} ({listing.city}), {listing.nightly_price}/night, "
                f"{listing.cancellation_policy} policy"
            )
    container.close()


def reconcile(settings: Settings) -> None:
    container = build_container(settings)
    with transaction(container.session_factory) as session:
        run = run_reconciliation(session, container.services)
        print(run.summary)
    container.close()


def eval_disputes(settings: Settings) -> None:
    container = build_container(settings)
    report = run_dispute_evals(container.services.mediator)
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        print(
            f"  {mark}  {result.name:<45} recommended {result.recommended_minor:>6}  "
            f"expected {result.expected_min}-{result.expected_max}"
        )
    print(
        f"{report.passed}/{len(report.results)} within range ({report.accuracy:.0%}), "
        f"mediator: {report.results[0].recommended_by if report.results else 'n/a'}"
    )
    container.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="settle", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("migrate", "apply database migrations"),
        ("seed", "create demo accounts and listings"),
        ("demo", "run the full story end to end on a throwaway database"),
        ("reconcile", "run every reconciliation check now"),
        ("eval-disputes", "score the dispute mediator against labelled cases"),
    ):
        sub.add_parser(name, help=text)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    match args.command:
        case "migrate":
            migrate(settings.database_url)
            print(f"Database is up to date: {settings.database_url}")
        case "seed":
            seed(settings)
        case "demo":
            if not run_demo(migrate):
                raise SystemExit("Demo finished in an unexpected state")
        case "reconcile":
            reconcile(settings)
        case "eval-disputes":
            eval_disputes(settings)


if __name__ == "__main__":
    main()
