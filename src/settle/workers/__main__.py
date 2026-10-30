"""Run the worker in its own process: ``python -m settle.workers``."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from settle.bootstrap import build_container
from settle.config import get_settings
from settle.workers.jobs import register_jobs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    container = build_container(get_settings())
    scheduler = BlockingScheduler(timezone="UTC")
    register_jobs(scheduler, container)
    logging.getLogger(__name__).info("Settle worker started")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        container.close()


if __name__ == "__main__":
    main()
