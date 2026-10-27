"""When will this link be done? Projected from what the field actually did.

The question every project manager asks the morning after launch — "at this
rate, when do we hit 400, and how much more sample do we need?" — answered by
arithmetic on the sessions, never by a model. The copilot explains the result;
it does not produce it.

The rate is completes per day. Recent days are preferred over the whole field
period, because a link speeds up and slows down (a panel's send, a weekend), and
the last few days say more about tomorrow than the first did. When the recent
window has too few completes to be a rate, the whole period is used instead, and
the result says which it used.

Starts needed come from what each complete actually cost in starts — incidence
AND drop-off together — not from incidence alone, which would undercount every
time someone quits halfway.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import ceil
from typing import Any

from src.engines.fielding.decisions import STATUS_COMPLETE, STATUS_TERMINATE

RECENT_DAYS = 3
MIN_RECENT_COMPLETES = 5  # fewer than this in the window is noise, not a rate
FEW_COMPLETES = 10  # below this a projection is shown with a warning
DAILY_ROWS = 14


@dataclass
class Goal:
    name: str  # "target", "hold", or a quota cell's name
    goal: int
    done: int
    remaining: int
    eta_days: float | None = None
    eta: str | None = None  # ISO date
    starts_needed: int | None = None


@dataclass
class Projection:
    starts: int
    completes: int
    incidence_pct: float | None
    starts_per_complete: float | None
    rate_per_day: float | None
    rate_basis: str  # what the rate was computed from, in words
    first_start: str | None
    goals: list[Goal] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)  # {day, starts, completes}
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def project(
    sessions: Iterable[Any],
    *,
    now: datetime | None = None,
    target: int | None = None,
    hold: int | None = None,
    quota_cells: Iterable[Any] = (),
) -> Projection:
    """Project a link's finish from its sessions.

    `sessions` need `status_code`, `started_at`, `completed_at`. `quota_cells`
    need `name` (or `id`), `limit` and `count`.
    """
    now = (now or datetime.now()).replace(tzinfo=None)
    sessions = list(sessions)
    starts = len(sessions)
    done = [s for s in sessions if s.status_code == STATUS_COMPLETE]
    completes = len(done)
    judged = completes + sum(1 for s in sessions if s.status_code == STATUS_TERMINATE)
    began = [t for s in sessions if (t := _when(getattr(s, "started_at", None)))]
    finished = [t for s in done if (t := _when(getattr(s, "completed_at", None)))]
    first = min(began) if began else None

    out = Projection(
        starts=starts, completes=completes,
        incidence_pct=round(100.0 * completes / judged, 1) if judged else None,
        starts_per_complete=round(starts / completes, 2) if completes else None,
        rate_per_day=None, rate_basis="", first_start=first.isoformat() if first else None,
    )

    # Completes per day: the last few days if they hold enough completes to be
    # a rate, else the whole period since the first start (at least a day, so
    # an hour-old link does not project 24x its first hour).
    recent_from = now - timedelta(days=RECENT_DAYS)
    recent = [t for t in finished if t >= recent_from]
    if len(recent) >= MIN_RECENT_COMPLETES and first and first <= recent_from:
        out.rate_per_day = round(len(recent) / RECENT_DAYS, 2)
        out.rate_basis = f"{len(recent)} completes in the last {RECENT_DAYS} days"
    elif first and completes:
        days = max((now - first).total_seconds() / 86400, 1.0)
        out.rate_per_day = round(completes / days, 2)
        out.rate_basis = (f"{completes} completes over {days:.1f} days since the first start"
                          + (" (too few in the last days for a recent rate)"
                             if days > RECENT_DAYS else ""))
    else:
        out.rate_basis = "no completes yet"

    if completes and completes < FEW_COMPLETES:
        out.warnings.append(f"Only {completes} completes so far: treat the projection as rough.")
    if finished and out.rate_per_day and (now - max(finished)) > timedelta(days=2):
        out.warnings.append("No completes in the last two days: the link may have stalled, "
                            "so the projection assumes a pace it is not keeping.")

    def goal(name: str, aim: int, have: int) -> Goal:
        left = max(aim - have, 0)
        g = Goal(name=name, goal=aim, done=have, remaining=left)
        if left == 0:
            g.eta_days, g.eta, g.starts_needed = 0.0, now.date().isoformat(), 0
        elif out.rate_per_day:
            share = have / completes if name not in ("target", "hold") and completes else 1.0
            rate = out.rate_per_day * share
            if rate > 0:
                g.eta_days = round(left / rate, 1)
                g.eta = (now + timedelta(days=g.eta_days)).date().isoformat()
        if left and out.starts_per_complete and name in ("target", "hold"):
            g.starts_needed = ceil(left * out.starts_per_complete)
        return g

    if hold:
        out.goals.append(goal("hold", hold, completes))
    if target:
        out.goals.append(goal("target", target, completes))
    for cell in quota_cells:
        limit = getattr(cell, "limit", None)
        if limit:
            name = str(getattr(cell, "name", None) or getattr(cell, "id", "quota"))
            out.goals.append(goal(name, int(limit), int(getattr(cell, "count", 0) or 0)))
    if not out.goals:
        out.warnings.append("This link has no target, hold or quota to project towards; "
                            "set a target to get a finish date.")

    by_day: dict[str, dict] = {}
    for s in sessions:
        if t := _when(getattr(s, "started_at", None)):
            by_day.setdefault(t.date().isoformat(), {"starts": 0, "completes": 0})["starts"] += 1
    for t in finished:
        by_day.setdefault(t.date().isoformat(), {"starts": 0, "completes": 0})["completes"] += 1
    out.daily = [{"day": d, **v} for d, v in sorted(by_day.items())][-DAILY_ROWS:]
    return out


def _demo() -> None:
    from types import SimpleNamespace as S

    now = datetime(2026, 10, 27, 12)

    def sess(day_offset: float, status: int | None):
        start = now - timedelta(days=day_offset)
        return S(status_code=status, started_at=start.isoformat(),
                 completed_at=(start + timedelta(minutes=12)).isoformat() if status else None)

    # 10 days of field: 2 completes a day early on, 5 a day in the last 3 days,
    # one screen-out per complete.
    sessions = []
    for day in range(10, 3, -1):
        sessions += [sess(day, 10), sess(day, 10), sess(day, 20), sess(day, 20)]
    for day in (3, 2, 1):
        sessions += [sess(day - 0.5, code) for code in (10,) * 5 + (20,) * 5]
    cells = [S(name="Women", limit=40, count=12)]
    p = project(sessions, now=now, target=100, hold=50, quota_cells=cells)
    assert p.completes == 29 and p.starts == 58 and p.incidence_pct == 50.0
    assert p.rate_per_day == 5.0 and p.rate_basis == "15 completes in the last 3 days"
    hold, target, women = p.goals
    assert (hold.remaining, hold.eta_days, hold.starts_needed) == (21, 4.2, 42)
    assert (target.remaining, target.eta, target.starts_needed) == (71, "2026-11-10", 142)
    # A cell fills at its own share of the pace: 12 of 29 completes are women.
    assert women.remaining == 28 and women.eta_days == round(28 / (5.0 * 12 / 29), 1)
    assert not p.warnings and p.daily[-1] == {"day": "2026-10-27", "starts": 10, "completes": 5}

    young = project([sess(0.1, 10), sess(0.1, 20)], now=now, target=10)
    assert young.rate_per_day == 1.0  # an hour-old link is measured over a day, not an hour
    assert any("rough" in w for w in young.warnings)
    assert project([], now=now).rate_basis == "no completes yet"


if __name__ == "__main__":
    _demo()
    print("ok")
