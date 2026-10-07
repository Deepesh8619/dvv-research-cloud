"""The database address must name the driver that is actually installed.

SQLAlchemy 2.1 changed what a bare `postgresql://` means — from psycopg2 to
psycopg 3, which this image does not install. A fresh build took production
down with `No module named 'psycopg'`, and the rollback rebuilt into the same
crash.
"""

from api.config import _normalize_db_url


def test_a_bare_postgres_address_names_the_installed_driver():
    assert _normalize_db_url("postgresql://dvv:pw@db:5432/dvv") == (
        "postgresql+psycopg2://dvv:pw@db:5432/dvv"
    )
    assert _normalize_db_url("postgres://dvv:pw@db:5432/dvv") == (
        "postgresql+psycopg2://dvv:pw@db:5432/dvv"
    )


def test_an_explicit_driver_and_sqlite_are_left_alone():
    assert _normalize_db_url("postgresql+psycopg2://x@h/d") == "postgresql+psycopg2://x@h/d"
    assert _normalize_db_url("sqlite:///./dvv_research.db") == "sqlite:///./dvv_research.db"


def test_the_engine_can_be_built_for_the_production_address():
    """create_engine imports the driver without connecting — exactly where
    production crashed."""
    from sqlalchemy import create_engine

    create_engine(_normalize_db_url("postgresql://dvv:pw@db:5432/dvv"))
