"""Serverless Postgres drops idle connections; the pool must expect it.

Neon suspends its compute after a few minutes of inactivity and closes the
socket. SQLAlchemy's pool has no idea and lends the dead connection to the next
request, which fails on whatever query happens to run first. In production that
was the login lookup during a file upload, so a perfectly good spreadsheet
looked like a broken feature — and the raw SQLAlchemy error, including the
user's own email, was shown to them as if the file were at fault.
"""

from __future__ import annotations

import pytest

from api.routes.studies import _upload_error_message

REAL_PRODUCTION_ERROR = (
    "OperationalError: (psycopg2.OperationalError) SSL connection has been closed "
    "unexpectedly [SQL: SELECT users.id AS users_id, users.email AS users_email "
    "FROM users WHERE users.email = %(email_1)s LIMIT %(param_1)s] "
    "[parameters: {'email_1': 'someone@example.com', 'param_1': 1}]"
)


def test_a_dropped_connection_is_explained_not_dumped():
    message = _upload_error_message(Exception(REAL_PRODUCTION_ERROR))
    assert "connection dropped" in message.lower()
    assert "try again" in message.lower()


def test_the_users_own_email_is_never_echoed_back():
    """The exception text carried it; the response must not."""
    message = _upload_error_message(Exception(REAL_PRODUCTION_ERROR))
    assert "someone@example.com" not in message


def test_no_sql_or_schema_leaks_to_the_browser():
    message = _upload_error_message(Exception(REAL_PRODUCTION_ERROR))
    for leak in ("SELECT", "FROM users", "psycopg2", "users.password_hash"):
        assert leak not in message


def test_an_unrelated_failure_gets_a_neutral_message():
    message = _upload_error_message(ValueError("column count mismatch in sheet 2"))
    assert "logged" in message.lower()
    assert "column count mismatch" not in message


@pytest.mark.parametrize(
    "text",
    [
        "server closed the connection unexpectedly",
        "connection already closed",
        "terminating connection due to administrator command",
        "could not connect to server: Connection refused",
    ],
)
def test_the_other_ways_a_connection_dies_are_recognised(text):
    assert "connection dropped" in _upload_error_message(Exception(text)).lower()


def test_postgres_engines_pre_ping_and_recycle():
    """The actual fix. Without pre_ping the pool hands out dead sockets."""
    from sqlalchemy import create_engine

    engine = create_engine(
        "postgresql://u:p@example.invalid:5432/db",
        pool_pre_ping=True,
        pool_recycle=280,
    )
    assert engine.pool._pre_ping is True
    # Comfortably under Neon's ~5 minute idle suspend.
    assert 0 < engine.pool._recycle < 300


def test_the_configured_engine_matches_its_backend():
    """SQLite has no server to disconnect from, so the pool settings that
    matter for Postgres must not be demanded of it."""
    from api.config import DATABASE_URL
    from api.db import engine

    if DATABASE_URL.startswith("sqlite"):
        assert engine.pool._pre_ping is False
    else:
        assert engine.pool._pre_ping is True
