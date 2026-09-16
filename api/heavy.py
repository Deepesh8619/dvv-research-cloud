"""CPU-heavy work runs in a separate pool of processes, never in the web process.

Measured with scripts/bench_concurrency.py: with heavy work in the web process,
a respondent opening a survey link waited 2.6 s while four analysts ran dry
runs, against 12 ms idle. Threads don't fix it — one process has one
interpreter lock — and extra web workers don't either: the worker whose event
loop is free accepts every connection, so all the CPU work lands on it anyway.
Moving the computation to its own processes is what makes respondent latency
independent of analyst load.

`spawn`, not `fork`: forking a process that has threads and open database
connections copies both in an undefined state. The first heavy call pays for
starting the workers; after that they are reused.

HEAVY_WORKERS=0 runs everything inline — tests, and machines where extra
processes aren't wanted.
"""

import multiprocessing
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, TypeVar

T = TypeVar("T")

# ponytail: one pool per web worker; a shared queue across hosts comes with the job queue.
WORKERS = int(os.getenv("HEAVY_WORKERS", str(max(1, min((os.cpu_count() or 2) - 1, 4)))))

_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                max_workers=WORKERS, mp_context=multiprocessing.get_context("spawn")
            )
        return _pool


def run_heavy(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run `fn` in the process pool and wait for it (the caller is a pool thread).

    `fn` must be a top-level function and every argument picklable — pass plain
    data, never SQLAlchemy rows or open sessions.
    """
    if WORKERS == 0:
        return fn(*args, **kwargs)
    try:
        return _get_pool().submit(fn, *args, **kwargs).result()
    except BrokenProcessPool:
        # A worker died (out of memory, killed). The pool is unusable from here
        # on, so replace it; this request fails, the next one gets a fresh pool.
        global _pool
        with _lock:
            _pool = None
        raise


def run_heavy_detached(db: Any, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Give the database connection back, THEN wait for the heavy work.

    A request that waits on the pool while still holding its connection starves
    everyone else: on a 2-core server one heavy worker serves ~20 queued field
    reports, each holding a connection for tens of seconds, and the next request
    dies with "QueuePool limit ... reached" (seen live at 200 users — 12% of
    requests failed). By this point the data is already loaded, so the
    connection has nothing left to do. The session reopens by itself if the
    caller queries again afterwards.
    """
    db.close()
    return run_heavy(fn, *args, **kwargs)


def plain_sessions(sessions: Any) -> list[Any]:
    """Respondent sessions as plain picklable objects for run_heavy.

    Only the attributes the fielding engines read. ORM rows stay in the web
    process, where their database session lives.
    """
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            id=s.id,
            status_code=s.status_code,
            started_at=s.started_at,
            completed_at=s.completed_at,
            last_seen_at=getattr(s, "last_seen_at", None),
            entry_params=dict(s.entry_params or {}),
        )
        for s in sessions
    ]
