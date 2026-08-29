"""Load a local .env file into os.environ.

Everything in this codebase reads configuration with `os.getenv`, which works
on Render (real environment variables) but silently ignored a local `.env` —
so a developer could add GROQ_API_KEY to `.env`, see `llm_keys: false`, and
have nothing explain why.

Deliberately stdlib rather than python-dotenv: parsing `KEY=value` with
comments and optional quotes is a dozen lines, and this avoids a dependency in
the import path of every entry point.

REAL ENVIRONMENT VARIABLES ALWAYS WIN. Values are applied with setdefault, so a
stale `.env` on a deployed box can never override what the platform actually
set — the failure mode that makes "works locally, wrong in production" so hard
to see.
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_loaded = False


def load_env(path: Path | None = None, *, force: bool = False) -> dict[str, str]:
    """Read `.env` into os.environ without clobbering real env vars.

    Idempotent — safe to call from several entry points, which is how it gets
    in front of the module-level `os.environ` reads in api/config.py and
    src/ai/router.py. Returns the parsed pairs (for tests/debugging); a missing
    file is not an error, it just means nothing to load.
    """
    global _loaded
    if _loaded and not force:
        return {}
    _loaded = True

    env_path = path or _PROJECT_ROOT / ".env"
    try:
        raw = env_path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}

    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if not key:
            continue
        value = value.strip()
        # Strip one layer of matching quotes; leave inner content untouched so
        # a token containing '#' or spaces survives.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif "#" in value:
            # Only an UNQUOTED value can carry a trailing comment.
            value = value.split("#", 1)[0].strip()
        if not value:
            continue
        parsed[key] = value
        os.environ.setdefault(key, value)
    return parsed
