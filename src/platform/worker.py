"""Worker loop: `python -m src.platform.worker`."""

import logging
import os
import signal
import threading
import time
import uuid

from src.platform import jobs

logger = logging.getLogger(__name__)


def run_once(session_factory, worker_id: str) -> bool:
    """Run one job if any is due. True if it ran one."""
    db = session_factory()
    try:
        job = jobs.claim(db, worker_id)
        if job is None:
            return False
        stop = threading.Event()

        def beat():
            while not stop.wait(30):
                with session_factory() as s:
                    jobs.heartbeat(s, job.id)

        threading.Thread(target=beat, daemon=True).start()
        try:
            jobs.run_one(db, job)
        finally:
            stop.set()
        return True
    finally:
        db.close()


def main() -> None:
    from api.db import SessionLocal

    worker_id = f"{os.uname().nodename}-{uuid.uuid4().hex[:6]}"
    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_reap = 0.0
    while running:
        if time.monotonic() - last_reap > 60:
            with SessionLocal() as db:
                n = jobs.reap(db)
            if n:
                logger.warning("reaped %d stale jobs", n)
            last_reap = time.monotonic()
        try:
            busy = run_once(SessionLocal, worker_id)
        except Exception:
            logger.exception("worker loop error")
            busy = False
        if not busy:
            time.sleep(1)


if __name__ == "__main__":
    main()
