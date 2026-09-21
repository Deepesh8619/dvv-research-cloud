"""Which commit is running — the one question a deploy has to answer honestly.

This was wrong in production on 2026-09-21: the box served code from that day's
merge while /api/health reported the previous day's commit. A version number
that is believed and wrong is worse than one that says "unknown".
"""

import src.utils.build_info as build_info
from src.utils.build_info import UNKNOWN, build_commit


def test_the_commit_comes_from_inside_the_image(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    stamp = tmp_path / ".build-commit"
    stamp.write_text("1c0088ab\n")
    assert build_commit(stamp) == "1c0088ab"


def test_a_stale_environment_variable_cannot_override_the_image(tmp_path, monkeypatch):
    """The exact production failure: the env said yesterday, the image was today."""
    monkeypatch.setenv("GIT_COMMIT", "723959b")
    stamp = tmp_path / ".build-commit"
    stamp.write_text("1c0088ab")
    assert build_commit(stamp) == "1c0088ab"


def test_the_platforms_own_variable_still_wins_where_a_platform_sets_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456789")
    stamp = tmp_path / ".build-commit"
    stamp.write_text("1c0088ab")
    assert build_commit(stamp) == "abc123def456"  # 12 characters, from the platform


def test_an_old_image_with_only_the_env_still_reports_something(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "723959b")
    assert build_commit(tmp_path / "absent") == "723959b"


def test_no_stamp_and_no_variable_says_unknown_rather_than_guessing(tmp_path, monkeypatch):
    for variable in ("GIT_COMMIT", "RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA"):
        monkeypatch.delenv(variable, raising=False)
    assert build_commit(tmp_path / "absent") == UNKNOWN
    (tmp_path / "empty").write_text("   \n")
    assert build_commit(tmp_path / "empty") == UNKNOWN


def test_the_file_it_reads_is_the_one_the_deploy_script_writes():
    """Both sides of this agreement are in different languages; if one moves
    the file and the other does not, the field silently reads "unknown"."""
    from pathlib import Path

    assert build_info.COMMIT_FILE.name == ".build-commit"
    script = Path("deploy/dvv").read_text()
    assert "> ../.build-commit" in script
    # …and the image must actually carry it: never dockerignored.
    assert ".build-commit" not in Path(".dockerignore").read_text()
