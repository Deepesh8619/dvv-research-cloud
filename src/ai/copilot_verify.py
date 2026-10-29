"""Every figure in a reply must come from what the tools returned.

The grounding check catches an answer that looked nothing up; the significance
check catches "significant" with no test. Neither catches a reply that looked
things up and then wrote a number none of them returned — a misread cell, a
sum the model did itself, a percentage from memory. This finds those.

Deliberately narrow, so it fires on real inventions and not on ids or dates:
percentages, decimals, and whole numbers of 10 or more that are not part of a
name (Q12, S4b) or a date. A figure counts as traced when any tool result
holds it — as written, rounded, or as a share ×100.
"""

from __future__ import annotations

import json
import re

_PERCENT = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s?%")
_DECIMAL = re.compile(r"(?<![\w.])(\d+\.\d+)(?![\w.])")
_WHOLE = re.compile(r"(?<![\w.,/-])(\d{2,3}(?:,\d{3})+|\d{2,})(?![\w.%/-])")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:19|20)\d{2}\b")
_IN_RESULT = re.compile(r"-?\d+(?:\.\d+)?")


def _figures(text: str) -> set[str]:
    text = _DATE.sub(" ", text)
    found = set(_PERCENT.findall(text)) | set(_DECIMAL.findall(text))
    found |= {w.replace(",", "") for w in _WHOLE.findall(text)}
    return found


def _supported(results: list[dict], also: str = "") -> set[float]:
    """Every number the tools returned, with the forms a reply may fairly use."""
    blob = json.dumps(results, default=str) + " " + also
    out: set[float] = set()
    for raw in _IN_RESULT.findall(blob):
        x = float(raw)
        out |= {x, round(x), round(x, 1), round(x * 100), round(x * 100, 1)}
    return out


def untraced(reply: str, results: list[dict], context: str = "") -> list[str]:
    """Figures in `reply` that no tool result (nor the user's own words in
    `context`) holds. Empty when every figure is traced, or nothing was looked up."""
    if not results:
        return []  # nothing looked up is the grounding check's job
    supported = _supported(results, context)
    missing = []
    for fig in sorted(_figures(reply), key=lambda f: reply.find(f)):
        x = float(fig)
        if not ({x, round(x), round(x, 1)} & supported):
            missing.append(fig)
    return missing


if __name__ == "__main__":
    res = [{"rows": [["Very satisfied", "36.4%", 0.227]], "n": 122, "id": "Q12"}]
    assert untraced("36.4% are very satisfied; 22.7% somewhat; base 122.", res) == []
    assert untraced("36% very satisfied", res) == []  # rounded
    assert untraced("about 41% are satisfied, from 122 people", res) == ["41"]
    assert untraced("Q12 and S4b on 2026-10-29", res) == []  # ids and dates are not figures
    assert untraced("62% incidence", []) == []
    print("ok")
