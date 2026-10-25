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


def drain(session_factory, worker_id: str = "drain") -> None:
    """Run every due job, then return. For tests, which have no worker thread."""
    while run_once(session_factory, worker_id):
        pass


def _loop(session_factory, worker_id: str, stop: threading.Event) -> None:
    last_reap = 0.0
    while not stop.is_set():
        if time.monotonic() - last_reap > 60:
            with session_factory() as db:
                n = jobs.reap(db)
            if n:
                logger.warning("reaped %d stale jobs", n)
            last_reap = time.monotonic()
        try:
            busy = run_once(session_factory, worker_id)
        except Exception:
            logger.exception("worker loop error")
            busy = False
        if not busy:
            stop.wait(1)


def _register_handlers() -> None:
    import api.routes.questionnaire  # noqa: F401 - importing registers their handlers
    import api.routes.studies  # noqa: F401


def start_background(session_factory) -> threading.Thread:
    """Run the worker loop in a daemon thread, so production needs no extra service.
    Set `thread.stop_event` to stop it."""
    _register_handlers()
    stop = threading.Event()
    t = threading.Thread(
        target=_loop,
        args=(session_factory, f"inproc-{uuid.uuid4().hex[:6]}", stop),
        daemon=True,
        name="dvv-worker",
    )
    t.stop_event = stop
    t.start()
    return t


def main() -> None:
    from api.db import SessionLocal

    _register_handlers()
    worker_id = f"{os.uname().nodename}-{uuid.uuid4().hex[:6]}"
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    _loop(SessionLocal, worker_id, stop)


if __name__ == "__main__":
    main()
