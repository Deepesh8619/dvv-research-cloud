"""Findings read off the computed tables, every number cited to its cell.

A deck's headline is where an invented number does the most harm: it is the
sentence a client repeats. The deck used to ask a model for each headline and
print it unchecked (and "No headline" when no model was reachable). Findings
here are computed from the tables themselves — the leading answer, the most
chosen options of a multi-select, a significant difference between banner
columns — and each carries citations (table, row, column, value, base). A
model's sentence is used only if every number in it is a cell, base or mean of
its table (`unverified_numbers`).

Works on the table JSON every viewer shares (`runner.table_to_dict`), so it
runs on stored crosstabs without recomputing anything.
"""

from __future__ import annotations

import re
from typing import Any

from src.engines.tabs.intervals import differs

# A multi-select comes out as one table per option: Q5r1, Q5r2, ...
_OPTION_STUB = re.compile(r"^(?P<q>.+?)r\d+$")
_CHECKED = {"checked", "selected", "yes", "mentioned"}
#: Fewer respondents than this in a column: its percentages are indicative only.
MIN_BASE = 30


def _cite(t: dict, row: str, col: int, value: float) -> dict[str, Any]:
    c = t["columns"][col]
    return {"table": t["id"], "row": row, "column": c["label"], "value": value,
            "base": c["unweighted_base"]}


def _pct(v: float) -> str:
    return f"{v:.0f}%"


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def checked_row(t: dict) -> dict | None:
    """The 'Checked' row of a one-option table, or None if it is not one."""
    rows = [r for r in t["rows"] if r["kind"] == "category"]
    if len(rows) != 2:
        return None
    hit = [r for r in rows if r["label"].strip().lower() in _CHECKED]
    return hit[0] if hit else None


def _lead(t: dict) -> dict | None:
    rows = sorted((r for r in t["rows"] if r["kind"] == "category"), key=lambda r: -r["pct"][0])
    # Everyone gave one answer: a quota or a constant, nothing to say.
    if len(rows) < 2 or rows[0]["pct"][0] <= 0 or rows[0]["pct"][0] >= 99.5:
        return None
    top, nxt = rows[0], rows[1]
    c = t["columns"][0]
    n = c.get("effective_base") or c["unweighted_base"]
    # Two answers of the same respondents: test the difference, not overlapping intervals.
    level = differs(top["pct"][0], nxt["pct"][0], n) is False
    verb = (f"is close to {nxt['label']} at {_pct(nxt['pct'][0])}" if level
            else f"leads at {_pct(top['pct'][0])}, ahead of {nxt['label']} "
                 f"at {_pct(nxt['pct'][0])}")
    return {
        "kind": "lead", "tables": [t["id"]],
        "text": f"{_short(t['title'])}: {top['label']} "
                + (f"at {_pct(top['pct'][0])} {verb}." if level else f"{verb}."),
        "strength": top["pct"][0],
        "cites": [_cite(t, r["label"], 0, r["pct"][0]) for r in (top, nxt)],
    }


def _option(title: str) -> str:
    """An option table's own label: "Which have you heard of? — Nescafé" is Nescafé."""
    return title.rsplit(" — ", 1)[-1]


def _multi(question: str, tables: list[dict]) -> dict | None:
    picks = sorted(((t, r) for t in tables if (r := checked_row(t))),
                   key=lambda p: -p[1]["pct"][0])
    if len(picks) < 2:
        return None
    top = picks[:3]
    said = ", then ".join(f"{_option(t['title'])} {_pct(r['pct'][0])}" for t, r in top)
    return {
        "kind": "most_chosen", "tables": [t["id"] for t, _r in picks],
        "text": f"{_short(question)}: most chosen {said}.",
        "strength": top[0][1]["pct"][0],
        "cites": [_cite(t, _option(t["title"]), 0, r["pct"][0]) for t, r in top],
    }


def _difference(t: dict, title: str | None = None) -> dict | None:
    """The largest significant gap between two banner columns in this table."""
    letters = {c["letter"]: j for j, c in enumerate(t["columns"]) if c.get("letter")}
    best = None
    for r in t["rows"]:
        for j, beaten in enumerate(r.get("sig") or []):
            for letter in beaten or "":
                k = letters.get(letter)
                if k is None:
                    continue
                gap = r["pct"][j] - r["pct"][k]
                if best is None or gap > best[0]:
                    best = (gap, r, j, k)
    if best is None:
        return None
    gap, r, j, k = best
    hi, lo = t["columns"][j], t["columns"][k]
    what = "" if r["label"].strip().lower() in _CHECKED else f" — {r['label']}"
    return {
        "kind": "difference", "tables": [t["id"]],
        "text": f"{_short(title or t['title'])}{what}: {_pct(r['pct'][j])} among {hi['label']} "
                f"against {_pct(r['pct'][k])} among {lo['label']}, a significant difference.",
        "strength": 100 + gap,  # a tested difference outranks any description
        "cites": [_cite(t, r["label"], j, r["pct"][j]), _cite(t, r["label"], k, r["pct"][k])],
    }


def _caution(t: dict) -> str | None:
    small = [c["label"] for c in t["columns"] if c["unweighted_base"] < MIN_BASE]
    return f"Base under {MIN_BASE} for {', '.join(small)}: indicative only." if small else None


def findings(tables: list[dict], questions: dict[str, str] | None = None,
             screeners: set[str] | None = None) -> list[dict]:
    """Every finding, strongest first.

    `questions` (qid -> wording) names a multi-select split into option tables;
    `screeners` are the qids that screen people out — what they show is who was
    let in, so their findings are the sample's profile, ranked last.
    """
    questions, screeners = questions or {}, screeners or set()
    out: list[dict] = []
    groups: dict[str, list[dict]] = {}
    for t in tables:
        m = _OPTION_STUB.match(t["stub"])
        if m and checked_row(t):
            groups.setdefault(m["q"], []).append(t)
            continue
        for f in (_difference(t), _lead(t)):
            if f:
                f["caution"], f["qid"] = _caution(t), t["stub"]
                out.append(f)
                break  # one finding per table: the tested difference if there is one
    for q, ts in groups.items():
        name = questions.get(q, q)
        if f := _multi(name, ts):
            f["caution"], f["qid"] = _caution(ts[0]), q
            out.append(f)
        for t in ts:  # an option chosen more by one banner column than another
            if f := _difference(t, f"{_short(name, 60)} {_option(t['title'])}"):
                f["caution"], f["qid"] = _caution(t), q
                out.append(f)
    for f in out:
        if f["qid"] in screeners:
            f["kind"], f["strength"] = "sample", f["strength"] - 1000
    return sorted(out, key=lambda f: -f["strength"])


_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|pp\b|points?\b)?")


def unverified_numbers(text: str, tables: list[dict]) -> list[str]:
    """Numbers in `text` that are no percentage, base or mean of `tables`."""
    rows = [r for t in tables for r in t["rows"]]
    pcts = [p for r in rows for p in r["pct"]]
    bases = [v for t in tables for c in t["columns"] for v in (c["base"], c["unweighted_base"])]
    means = [m for t in tables for m in t.get("means") or []]
    gaps = [abs(a - b) for t in tables for r in t["rows"] for s in t["rows"]
            for a in r["pct"] for b in s["pct"]]
    return unmatched_numbers(text, tables, pcts, bases, means, gaps)


def unmatched_numbers(text: str, tables: list[dict], pcts: list[float], bases: list[float],
                      means: list[float], gaps: list[float]) -> list[str]:
    """Numbers in `text` that match none of the allowed values. `tables` only supply
    the labels to strip: "Under 35", "250-499": numbers in a label are not claims."""
    labels = {x for t in tables for x in [t["title"], *(r["label"] for r in t["rows"]),
                                           *(c["label"] for c in t["columns"])]}
    for label in sorted(labels, key=len, reverse=True):
        # Bounded, so a row labelled "1" never eats the 1 of "71%".
        text = re.sub(rf"(?<![\d.]){re.escape(label)}(?!\d|\.\d|%)", " ", text)
    bad = []
    for raw, unit in _NUMBER.findall(text):
        x = float(raw.replace(",", ""))
        if unit == "%":
            ok = any(abs(x - p) < 0.51 for p in pcts)
        elif unit:  # percentage points
            ok = any(abs(x - g) < 1.01 for g in gaps)
        else:
            ok = x <= 10 or any(abs(x - b) < 0.51 for b in bases + pcts) or any(
                abs(x - m) < 0.051 for m in means)
        if not ok:
            bad.append(raw + unit)
    return bad
