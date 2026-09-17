"""The client-stack CLI: it must stamp the build, and refuse the wrong user.

Both rules came from running `./dvv upgrade` on a real server for the first
time: the deployment reported `"commit": "unknown"` (so no one could tell which
version a client was running), and running it under sudo took a backup and then
died on "Host key verification failed", which reads like a broken deploy key.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DVV = ROOT / "deploy" / "dvv"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"


def test_the_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(DVV)], check=True)


def test_the_image_records_the_commit_it_was_built_from():
    dockerfile = DOCKERFILE.read_text()
    assert "ARG GIT_COMMIT=unknown" in dockerfile
    assert "ENV GIT_COMMIT=$GIT_COMMIT" in dockerfile
    # ...and it is declared AFTER the source copy, so editing code does not
    # rebuild the world and a new commit does not bust every earlier layer.
    assert dockerfile.index("COPY . .") < dockerfile.index("ARG GIT_COMMIT")


def test_compose_passes_the_commit_into_the_build():
    assert "GIT_COMMIT: ${GIT_COMMIT:-unknown}" in COMPOSE.read_text()


def test_every_build_goes_through_the_stamping_helper():
    """A bare `dc up -d --build` would produce an unstamped image."""
    body = DVV.read_text()
    helper = body[body.index("build_app() {") : body.index("health() {")]
    assert "GIT_COMMIT=$(git rev-parse --short HEAD" in helper
    others = [
        line.strip()
        for line in body.splitlines()
        if "up -d --build" in line and "build_app()" not in line
    ]
    assert others == ["dc up -d --build \"$@\""], others


def test_root_is_refused_when_the_checkout_belongs_to_someone_else():
    """The guard must fire BEFORE any command runs — a backup taken and then
    abandoned is worse than a clean refusal."""
    body = DVV.read_text()
    guard = body[: body.index("dc() {")]
    assert 'id -u' in guard and '"0"' in guard
    assert "sudo -u" in guard  # tells the operator what to type instead
    # It sits above the command dispatch, not inside one branch of it.
    assert body.index("id -u") < body.index('case "${1:-}"')


def test_the_guard_lets_a_normal_user_through():
    """Run the real script as this (non-root) user: it must reach the usage
    text, not the refusal."""
    result = subprocess.run(
        ["bash", str(DVV), "definitely-not-a-command"],
        capture_output=True,
        text=True,
        cwd=DVV.parent,
    )
    assert "not root" not in result.stdout
    assert re.search(r"usage|\./dvv", result.stdout + result.stderr, re.I)


def test_the_commit_actually_reaches_the_child_process():
    """Run the real helper with `dc` stubbed, and read the child's environment.

    Asserting the text is present proves nothing about whether docker compose
    can see the variable — one misplaced quote or a missing export and every
    image is stamped "unknown" while the test still passes. This runs the
    helper for real and looks at what the child process inherits.
    """
    body = DVV.read_text()
    helper = body[body.index("build_app() {") : body.index("health() {")]
    script = f'dc() {{ env | grep "^GIT_COMMIT=" || echo MISSING; }}\n{helper}\nbuild_app app\n'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=ROOT
    )
    assert "MISSING" not in result.stdout, "docker compose would not see GIT_COMMIT"
    assert re.match(r"GIT_COMMIT=[0-9a-f]{7,}", result.stdout.strip()), result.stdout


def test_an_upgrade_reports_which_studies_it_did_not_reach():
    """A parser fix only changes the NEXT extraction. A client whose stored
    study is missing its screen-outs has no way to know unless the upgrade says
    so — and a questionnaire that screens nobody out will field and pay for
    every respondent who should have been turned away."""
    body = DVV.read_text()
    upgrade = body[body.index("  upgrade)") : body.index("  offsite)")]
    assert "audit_definitions.py" in upgrade
    # Only after the health check passed: reporting stale studies while rolling
    # back would bury the thing that actually went wrong.
    assert upgrade.index("wait_healthy") < upgrade.index("audit_definitions.py")
    # Findings are exit 1 and are NOT an upgrade failure.
    assert "audit_status" in upgrade and "-gt 1" in upgrade


def test_the_audit_is_also_a_command_of_its_own():
    body = DVV.read_text()
    assert "\n  audit)" in body
    assert "./dvv audit" in body  # and it is in the usage header
