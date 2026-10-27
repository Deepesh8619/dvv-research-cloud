"""Scoring for the copilot eval set (evals/copilot/cases.json).

A case is one conversation: one or more turns, each with what a good answer
must do. Checks are mechanical on purpose — which tools ran, which cards came
back, which facts the reply states — so a score means the same thing on every
run and a change of model or prompt can be compared with the last one.

Expected figures are not written into the cases. They are named
(`{incidence_a}`) and filled in at run time from the platform's own reports on
the seeded project, so a case can never drift from what the engines say.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

CARD_KINDS = frozenset({
    "open_link", "hold", "release", "close", "materialize", "reextract",
    "look", "approve_clean", "quota",
})
EXPECT_KEYS = frozenset({
    "tools", "tools_any", "no_tools", "cards", "cards_allowed", "card_mentions",
    "mentions", "avoids", "may_skip_tools",
})
_PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")


def load_cases(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["cases"]


def problems(cases: list[dict], tool_names: frozenset[str]) -> list[str]:
    """Everything wrong with the case file, so a typo fails a test, not a run."""
    found: list[str] = []
    seen: set[str] = set()
    for case in cases:
        cid = case.get("id", "?")
        if cid in seen:
            found.append(f"{cid}: duplicate id")
        seen.add(cid)
        if not case.get("category"):
            found.append(f"{cid}: no category")
        if not case.get("turns"):
            found.append(f"{cid}: no turns")
        for turn in case.get("turns", []):
            if not str(turn.get("say", "")).strip():
                found.append(f"{cid}: a turn says nothing")
            expect = turn.get("expect", {})
            for key in set(expect) - EXPECT_KEYS:
                found.append(f"{cid}: unknown check {key!r}")
            for key in ("tools", "tools_any", "no_tools"):
                for name in expect.get(key, []):
                    if name not in tool_names:
                        found.append(f"{cid}: no tool {name!r}")
            for key in ("cards", "cards_allowed"):
                for kind in expect.get(key, []):
                    if kind not in CARD_KINDS:
                        found.append(f"{cid}: no card kind {kind!r}")
            for key in ("mentions", "avoids", "card_mentions"):
                for pattern in expect.get(key, []):
                    try:
                        re.compile(_PLACEHOLDER.sub("0", pattern))
                    except re.error as exc:
                        found.append(f"{cid}: bad pattern {pattern!r} ({exc})")
    return found


def number_pattern(value: float | int) -> str:
    """A figure as a model may fairly write it: 61.5 as "61.5" or "62"."""
    shown = {f"{value:.1f}".rstrip("0").rstrip("."), str(round(value))}
    alternatives = "|".join(re.escape(s) for s in sorted(shown, key=len, reverse=True))
    return rf"(?<![\d.])(?:{alternatives})(?!\.?\d)"


def fill(pattern: str, facts: dict[str, str]) -> str:
    """Replace {name} with the fact's regex; an unknown name is a run error."""
    return _PLACEHOLDER.sub(lambda m: f"(?:{facts[m.group(1)]})", pattern)


def fill_text(text: str, facts: dict[str, str], literal: dict[str, str]) -> str:
    """A turn's message with ids in it ("hold {link_b} at 20")."""
    return _PLACEHOLDER.sub(lambda m: literal.get(m.group(1), m.group(0)), text)


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) is not None


def score_turn(expect: dict, response: dict, facts: dict[str, str]) -> list[str]:
    """The checks this answer failed, in words. Empty means it passed."""
    if response.get("error"):
        return [f"request failed: {response['error']}"]
    failures: list[str] = []
    reply = response.get("reply", "")
    used = set(response.get("tools_used", []))
    kinds = [c.get("kind") for c in response.get("proposals", [])]

    for name in expect.get("tools", []):
        if name not in used:
            failures.append(f"did not call {name}")
    if expect.get("tools_any") and not used & set(expect["tools_any"]):
        failures.append(f"called none of {expect['tools_any']}")
    for name in expect.get("no_tools", []):
        if name in used:
            failures.append(f"called {name}, which it should not")
    if "cards" in expect and sorted(kinds) != sorted(expect["cards"]):
        failures.append(f"cards {kinds}, expected {expect['cards']}")
    if "cards_allowed" in expect and set(kinds) - set(expect["cards_allowed"]):
        failures.append(f"cards {kinds}, only {expect['cards_allowed']} allowed")
    titles = " ".join(f"{c.get('title', '')} {c.get('detail', '')}"
                      for c in response.get("proposals", []))
    for pattern in expect.get("card_mentions", []):
        if not _has(fill(pattern, facts), titles):
            failures.append(f"no card says {pattern!r}")
    for pattern in expect.get("mentions", []):
        if not _has(fill(pattern, facts), reply):
            failures.append(f"reply does not say {pattern!r}")
    for pattern in expect.get("avoids", []):
        if _has(fill(pattern, facts), reply):
            failures.append(f"reply says {pattern!r}, which it should not")
    # Every case, unless it says otherwise: figures need a lookup behind them.
    if not expect.get("may_skip_tools") and not used and re.search(r"\d", reply):
        failures.append("stated figures without looking anything up")
    return failures


def score_case(case: dict, responses: list[dict], facts: dict[str, str]) -> dict:
    failures: list[str] = []
    for i, (turn, response) in enumerate(zip(case["turns"], responses, strict=False)):
        failures += [f"turn {i + 1}: {f}" for f in score_turn(turn.get("expect", {}),
                                                              response, facts)]
    if len(responses) < len(case["turns"]):
        failures.append("conversation stopped early")
    return {
        "id": case["id"],
        "category": case["category"],
        "passed": not failures,
        "failures": failures,
        "seconds": [r.get("seconds") for r in responses],
        "answered_by": [r.get("answered_by") for r in responses],
        "replies": [r.get("reply", "") for r in responses],
        "tools_used": [r.get("tools_used", []) for r in responses],
    }


def summarize(results: list[dict]) -> dict[str, Any]:
    by_category: dict[str, list[bool]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["passed"])
    times = sorted(t for r in results for t in r["seconds"] if t is not None)
    providers: dict[str, int] = {}
    for r in results:
        for p in r["answered_by"]:
            if p:
                providers[p] = providers.get(p, 0) + 1
    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "score": round(100 * sum(r["passed"] for r in results) / max(len(results), 1), 1),
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_category.items())},
        "median_seconds": round(statistics.median(times), 1) if times else None,
        "p95_seconds": round(times[int(0.95 * (len(times) - 1))], 1) if times else None,
        "answered_by": providers,
    }
