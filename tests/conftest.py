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

import os
import sys
import urllib.error

import pytest

import src.ai.navigator  # noqa: F401 - loaded now so its by-name copy is covered below
import src.ai.router

# The real transport, captured before any test patches it. Modules that did
# `from src.ai.router import http_json` hold their OWN reference to this
# function; patching only `src.ai.router.http_json` left the navigator (the
# data chat, the copilot) free to reach the real network.
_REAL_HTTP_JSON = src.ai.router.http_json

# No worker thread in tests: they run queued jobs themselves with `drain(factory)`.
os.environ["DVV_INPROCESS_WORKER"] = "0"


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("exercises_http_json"):
        # `http_json` IS the subject of these tests; they stub the transport
        # underneath it themselves and must see the real function.
        return

    def _refuse(req, timeout):  # noqa: ARG001 - signature must match
        raise urllib.error.URLError("network disabled in tests")

    for name, module in list(sys.modules.items()):
        if name.startswith(("src.", "api.")) and (
            getattr(module, "http_json", None) is _REAL_HTTP_JSON
        ):
            monkeypatch.setattr(module, "http_json", _refuse)


@pytest.fixture(autouse=True)
def _clear_backend_cooldowns():
    """No test inherits another's backend cooldown.

    The router remembers a backend that just failed and skips it for a few
    minutes — deliberate in a running process, where it saves every chunk of a
    document from re-paying a dead local model's timeout. Shared module state
    across tests is a different thing: one test making Ollama fail silently
    changed what the next test saw, and it failed only in suite order.
    """
    from src.ai.router import _backend_cooldown

    _backend_cooldown.clear()
    yield
    _backend_cooldown.clear()
