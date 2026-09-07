"""Run VitaMine's hosted background-job worker outside the HTTP gateway."""

from __future__ import annotations

import signal
from types import FrameType

from vitamine.cloud_app import JOB_STOP, run_background_job_runner


def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    """Ask the active job to stop so it is safely requeued before exit."""
    JOB_STOP.set()


def main() -> None:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    run_background_job_runner()


if __name__ == "__main__":
    main()
