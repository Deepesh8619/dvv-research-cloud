"""A study whose pipeline worker died must stop claiming to be working.

The data pipeline persisted nothing until it finished and had no heartbeat, so
a worker killed part-way — a deploy, a free-tier spin-down, a crash — left the
study on "processing" for ever. The project page polled it, showed "QC
pending…", and a researcher coming back later found an empty project with no
way to tell that anything had gone wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from api.routes.studies import _STUDY_STALE_AFTER, _is_stale_study


def _study(status: str, age: timedelta, stage: str | None = None):
    return SimpleNamespace(
        status=status,
        updated_at=(datetime.now() - age).isoformat(),
        created_at=None,
        study_metadata={"stage": stage} if stage else {},
    )


def test_a_heartbeat_just_written_is_not_stale():
    """The guard must never fire on a run that is actively reporting progress."""
    assert _is_stale_study(_study("classifying", timedelta(seconds=0))) is False


def test_a_slow_but_beating_run_is_not_stale():
    """Coding and deck rendering legitimately take minutes on a large file."""
    assert _is_stale_study(_study("processing", _STUDY_STALE_AFTER - timedelta(minutes=2))) is False


def test_a_run_past_the_window_is_stale():
    assert _is_stale_study(_study("processing", _STUDY_STALE_AFTER + timedelta(minutes=1))) is True


def test_a_finished_study_is_never_stale():
    """A delivered study is old by definition; age alone must not condemn it."""
    for status in ("delivered", "rendered", "authoring", "error", "approved_for_delivery"):
        assert _is_stale_study(_study(status, timedelta(days=30))) is False, status


def test_an_unparseable_timestamp_is_not_stale():
    """A parse error must never mask a run that is still going."""
    broken = SimpleNamespace(
        status="processing", updated_at="not a date", created_at=None, study_metadata={}
    )
    assert _is_stale_study(broken) is False


def test_a_missing_timestamp_is_not_stale():
    absent = SimpleNamespace(
        status="processing", updated_at=None, created_at=None, study_metadata={}
    )
    assert _is_stale_study(absent) is False


def test_the_stage_heartbeat_survives_a_write_failure():
    """Bookkeeping must not be able to kill the pipeline it is measuring."""
    from api.db import touch_study_stage

    class Exploding:
        def query(self, *_a, **_k):
            raise RuntimeError("database gone")

        def rollback(self):
            pass

    touch_study_stage(Exploding(), "study_1", "quality check")  # must not raise
