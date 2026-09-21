"""Which commit the running instance was actually built from.

This exists because the field that answers "is my fix live?" gave the wrong
answer, and gave it confidently. On 2026-09-21 the box served a new frontend
bundle — code that only existed in that day's merge — while `/api/health` kept
reporting the commit from the day before. Reading a stale version number is
worse than reading none: it was believed, and it led to the conclusion that
the deploy had not happened when it had.

The commit used to travel as a Docker build ARG turned into an ENV. That is
three fragile hops — compose substitution (shell environment versus the
project's `.env`), the image's baked `ENV`, and `env_file:` which can override
it again at run time — and not one of them fails loudly.

So it travels as a FILE instead. `deploy/dvv` writes `.build-commit` into the
checkout immediately before building; `COPY . .` carries it into the image;
this reads it at run time. The file is part of the image's own contents, so an
image started next month still reports the code it actually contains, and
nothing in the environment can contradict it.

Platform variables still win where a platform sets them per deploy (Railway,
Render), because there the platform, not this script, is the source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Written by `deploy/dvv build_app` into the build context. Not committed.
COMMIT_FILE = Path(__file__).resolve().parents[2] / ".build-commit"

UNKNOWN = "unknown"


def build_commit(commit_file: Path | None = None) -> str:
    """The commit this instance is running, or "unknown" — never a guess."""
    for variable in ("RAILWAY_GIT_COMMIT_SHA", "RENDER_GIT_COMMIT"):
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value[:12]

    path = commit_file or COMMIT_FILE
    try:
        stamped = path.read_text(encoding="utf-8").strip().split()[0]
    except (OSError, IndexError):
        stamped = ""
    if stamped:
        return stamped[:12]

    # Last: the old ENV path. Kept so an image built before this change still
    # reports something, and deliberately last because it is the one that went
    # stale without saying so.
    return ((os.environ.get("GIT_COMMIT") or "").strip()[:12]) or UNKNOWN
