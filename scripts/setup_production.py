"""Bring a FRESH database up to a usable state, in one command.

Written for the rebuild after Render's free Postgres expired and took the API
down with it. Points at whatever `DATABASE_URL` says, so it works against a new
Neon/Supabase database from your laptop before the backend is even redeployed.

    DATABASE_URL='postgresql://...' python scripts/setup_production.py --check
    DATABASE_URL='postgresql://...' python scripts/setup_production.py \
        --founder "Vaibhav:vaibhav@dvvanalytics.com" \
        --founder "Vishal:vishal@dvvanalytics.com"

`--check` connects, reports the schema, and changes nothing — run it first.

SAFE TO RE-RUN. Every step is idempotent: existing users are left alone
(passwords are NEVER reset for someone who already exists), tables are only
created when missing. Nothing here deletes anything.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fail(msg: str) -> None:
    print(f"\n  FAILED: {msg}\n")
    raise SystemExit(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true",
                   help="Connect and report only — change nothing")
    p.add_argument("--founder", action="append", default=[], metavar="NAME:EMAIL",
                   help="Create a founder account (repeatable)")
    p.add_argument("--password", help="Password for created founders "
                                      "(generated and printed if omitted)")
    args = p.parse_args()

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        _fail("DATABASE_URL is not set. Paste your database's connection string:\n"
              "    DATABASE_URL='postgresql://...' python scripts/setup_production.py --check")
    if url.startswith("sqlite"):
        print("  NOTE: DATABASE_URL points at SQLite, not a hosted database.")

    # Imported AFTER the env check so config picks up DATABASE_URL.
    from sqlalchemy import inspect

    from api.db import UserDB, engine, init_db

    safe = url.split("@")[-1][:60] if "@" in url else url[:60]
    print(f"\nDatabase: ...@{safe}")

    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        _fail(
            f"could not connect: {exc}\n"
            "  - Neon/Supabase: use the POOLED connection string\n"
            "  - the URL must start postgresql:// (not postgres://)\n"
            "  - check the database is not paused in its dashboard"
        )
    print("  connection OK")

    existing = set(inspect(engine).get_table_names())
    print(f"  tables present before: {len(existing)}")

    if args.check:
        print("\n--check: nothing was changed.")
        if existing:
            print(f"  {', '.join(sorted(existing))}")
        return

    # Creates missing tables and runs the defensive ALTERs for columns added
    # after a table first shipped (see api/db.py::_ensure_new_columns).
    init_db()
    after = set(inspect(engine).get_table_names())
    print(f"  tables present after:  {len(after)}")
    if new := sorted(after - existing):
        print(f"  created: {', '.join(new)}")

    required = {"users", "studies", "questionnaires", "corrections",
                "profile_rules", "correction_previews", "ui_overrides", "audit_log"}
    if missing := sorted(required - after):
        _fail(f"expected tables still missing: {', '.join(missing)}")
    print("  all expected tables present")

    if not args.founder:
        print("\nNo --founder given, so no accounts were created.")
        return

    from api.auth import hash_password

    db = None
    try:
        from api.db import SessionLocal

        db = SessionLocal()
        password = args.password or secrets.token_urlsafe(12)
        created = []
        for spec in args.founder:
            if ":" not in spec:
                _fail(f"--founder must be NAME:EMAIL, got {spec!r}")
            name, email = (part.strip() for part in spec.split(":", 1))

            # Never touch an account that already exists — silently resetting
            # someone's password is exactly the bug that put `changeme123` on a
            # real admin account earlier in this project.
            if db.query(UserDB).filter(UserDB.email == email).first():
                print(f"  {email} already exists — left untouched")
                continue
            db.add(
                UserDB(
                    id=f"user_{secrets.token_hex(4)}",
                    email=email,
                    name=name,
                    password_hash=hash_password(password),
                    role="founder",
                    created_at=datetime.now(UTC).isoformat(),
                    is_active=True,
                    # This password was set deliberately, not synced from
                    # ADMIN_PASSWORD, so the env-sync must never overwrite it.
                    password_synced_from_env=False,
                )
            )
            created.append(email)
        db.commit()

        if created:
            print(f"\n  created {len(created)} founder account(s): {', '.join(created)}")
            if not args.password:
                print(f"\n  PASSWORD: {password}")
                print("  Shown once. Send it to them over something private, and have")
                print("  them change it on first sign-in.")
    finally:
        if db is not None:
            db.close()

    print("\nDone. Next: set DATABASE_URL (and GROQ_API_KEY, "
          "LLM_OPEN_WEIGHTS_ONLY=1) in the backend host's dashboard.")


if __name__ == "__main__":
    main()
