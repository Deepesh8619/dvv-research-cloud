"""Test-suite-wide guarantees.

The only one so far: no test reaches the network. Two orchestration tests were
calling a live model backend, which made the gate's runtime a function of a
third party's uptime — 45 seconds each on a good day, and on a bad one the
whole suite sat in a socket read for twenty minutes. A test that passes only
while an external API is up is not testing our code.

Every backend the router can reach goes through `http_json`, so refusing there
covers all of them at once. `run_task` already treats a refusal as
`BackendUnavailable`, which is the same path production takes when the
provider is down — so the tests exercise a real, supported state rather than a
fictional one. A test that genuinely needs a canned response patches
`run_task` itself, which takes precedence over this.
"""

from __future__ import annotations

import urllib.error

import pytest

import src.ai.router


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("exercises_http_json"):
        # `http_json` IS the subject of these tests; they stub the transport
        # underneath it themselves and must see the real function.
        return

    def _refuse(req, timeout):  # noqa: ARG001 - signature must match
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(src.ai.router, "http_json", _refuse)
