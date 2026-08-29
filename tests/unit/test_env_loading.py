"""Local .env loading.

Before this existed, everything read os.getenv directly and a local `.env` was
silently ignored — a developer could add GROQ_API_KEY, see `llm_keys: false`,
and have nothing explain why.

The load-bearing test is `test_real_environment_variables_win`: a stale .env on
a deployed box must never override what the platform actually set.
"""

from __future__ import annotations

import os

from src.env import load_env


def _write(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body)
    return p


def test_real_environment_variables_win(tmp_path, monkeypatch):
    """The platform's own env beats the file. Prevents 'works locally, wrong in prod'."""
    monkeypatch.setenv("DVV_TEST_KEY", "from-real-env")
    load_env(_write(tmp_path, "DVV_TEST_KEY=from-dotenv"), force=True)
    assert os.environ["DVV_TEST_KEY"] == "from-real-env"


def test_missing_value_is_skipped(tmp_path, monkeypatch):
    """An unfilled placeholder must not mask a key as 'configured but broken'."""
    monkeypatch.delenv("DVV_TEST_EMPTY", raising=False)
    parsed = load_env(_write(tmp_path, "DVV_TEST_EMPTY=\n"), force=True)
    assert "DVV_TEST_EMPTY" not in parsed
    assert "DVV_TEST_EMPTY" not in os.environ


def test_comments_blank_lines_and_export_prefix(tmp_path, monkeypatch):
    for k in ("DVV_A", "DVV_B"):
        monkeypatch.delenv(k, raising=False)
    parsed = load_env(
        _write(tmp_path, "# a comment\n\nexport DVV_A=one\nDVV_B=two   # trailing\n"),
        force=True,
    )
    assert parsed["DVV_A"] == "one"
    # An unquoted trailing comment is stripped.
    assert parsed["DVV_B"] == "two"


def test_quoted_value_keeps_hash_and_spaces(tmp_path, monkeypatch):
    """A quoted secret containing '#' must survive intact — tokens do contain them."""
    monkeypatch.delenv("DVV_TOKEN", raising=False)
    parsed = load_env(_write(tmp_path, 'DVV_TOKEN="ab#cd ef"\n'), force=True)
    assert parsed["DVV_TOKEN"] == "ab#cd ef"


def test_missing_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "nope.env", force=True) == {}


def test_is_idempotent_without_force(tmp_path, monkeypatch):
    monkeypatch.delenv("DVV_ONCE", raising=False)
    load_env(_write(tmp_path, "DVV_ONCE=1"), force=True)
    # Second call without force is a no-op, so repeated entry-point calls are free.
    assert load_env(_write(tmp_path, "DVV_ONCE=2")) == {}
