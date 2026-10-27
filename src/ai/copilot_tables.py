"""Tables the copilot shows, exactly as the engines computed them.

The data navigator returns tables in three shapes — a crosstab (with
significance letters) under `table`, several under `tables`, and a SQL result as
`columns` + `rows`. The copilot used to keep only the last, so a real crosstab
reached it as one sentence about it, and the model answered "a table of Q4" with
means it chose itself. Every shape becomes one plain table here: a header and
rows of strings, shown to the user under the reply — never retyped by a model.

Whether a significance test actually ran travels with each table, so a reply
that says "significant" (or "not significant") can be held to it.
"""

from __future__ import annotations

import re
from typing import Any

MAX_ROWS = 40

#: A sentence making a claim only a test can support: "statistically",
#: "significant(ly)", "significance", "p < 0.05".
SIGNIFICANCE_CLAIM = re.compile(
    r"\bstatistical(?:ly)?\b|\bsignifican(?:t|tly|ce)\b|\bp\s*[<=>]\s*0?\.\d", re.IGNORECASE
)


#: A sentence that mentions significance only to say it was not tested.
_NOT_TESTED = re.compile(
    r"not (?:been )?(?:tested|computed|calculated|run)|untested|no (?:significance )?test"
    r"|(?:can.?t|cannot|unable to|could not|couldn.?t) (?:say|tell|determine|confirm|assess)"
    r"|would need (?:a )?(?:significance )?test|whether (?:it|this|the difference) is",
    re.IGNORECASE)


def claims_significance(text: str) -> bool:
    """Does any sentence assert significance, either way, as a finding?"""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        if SIGNIFICANCE_CLAIM.search(sentence) and not _NOT_TESTED.search(sentence):
            return True
    return False


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _crosstab(t: dict) -> dict:
    cols = [c for c in t.get("columns") or [] if isinstance(c, dict)]
    header = [str(t.get("title") or t.get("stub") or "Table")] + [
        f"{c.get('label', '')} ({c['letter']})" if c.get("letter") else str(c.get("label", ""))
        for c in cols
    ]
    rows = [["Base"] + [_cell(c.get("base")) for c in cols]]
    for r in t.get("rows") or []:
        sig = r.get("sig") or [""] * len(r.get("pct") or [])
        rows.append([str(r.get("label", ""))] + [
            f"{_cell(p)}%" + (f" {s}" if s else "") for p, s in zip(r.get("pct") or [], sig,
                                                                    strict=False)])
    if t.get("means"):
        sig = t.get("mean_sig") or [""] * len(t["means"])
        rows.append(["Mean"] + [_cell(m) + (f" {s}" if s else "")
                                for m, s in zip(t["means"], sig, strict=False)])
    # Letters are only drawn between two or more lettered columns; a Total-only
    # table has had no test, whatever its percentages look like.
    tested = sum(1 for c in cols if c.get("letter")) >= 2
    return {"title": header[0], "header": header, "rows": rows[:MAX_ROWS],
            "truncated": len(rows) > MAX_ROWS, "significance_tested": tested}


def _query(result: dict) -> dict:
    columns = [str(c) for c in result.get("columns") or []]
    rows = []
    for r in result.get("rows") or []:
        values = [r.get(c) for c in columns] if isinstance(r, dict) else list(r)
        rows.append([_cell(v) for v in values])
    return {"title": "Query result", "header": columns, "rows": rows[:MAX_ROWS],
            "truncated": bool(result.get("truncated")) or len(rows) > MAX_ROWS,
            "significance_tested": False}


def tables_in(result: dict) -> list[dict]:
    """Every table one navigator tool result holds, in the one plain shape."""
    if not isinstance(result, dict):
        return []
    if isinstance(result.get("table"), dict):
        return [_crosstab(result["table"])]
    if isinstance(result.get("tables"), list):
        return [_crosstab(t) for t in result["tables"] if isinstance(t, dict)]
    if result.get("columns") and result.get("rows") is not None:
        return [_query(result)]
    return []


def _demo() -> None:
    crosstab = {"table": {
        "title": "Q4 Satisfaction", "columns": [
            {"label": "Total", "letter": "", "base": 22.0},
            {"label": "Man", "letter": "A", "base": 11.0},
            {"label": "Woman", "letter": "B", "base": 11.0}],
        "rows": [{"label": "Very satisfied", "pct": [27.3, 36.4, 18.2], "sig": ["", "B", ""]}],
        "means": [3.27, 3.5, 3.0], "mean_sig": ["", "", ""]}}
    (t,) = tables_in(crosstab)
    assert t["header"] == ["Q4 Satisfaction", "Total", "Man (A)", "Woman (B)"]
    assert t["rows"][1] == ["Very satisfied", "27.3%", "36.4% B", "18.2%"]
    assert t["significance_tested"] is True
    (q,) = tables_in({"columns": ["S2", "n"], "rows": [{"S2": 1, "n": 11}]})
    assert q["rows"] == [["1", "11"]] and q["significance_tested"] is False
    assert claims_significance("No statistically significant difference")
    assert not claims_significance("Women are less satisfied (3.0 vs 3.5).")
    assert not claims_significance("Whether the difference is significant has not been tested.")
    assert not claims_significance("I can't say if it is statistically significant.")
    assert claims_significance("Men score higher. The gap is significant at 95%.")


if __name__ == "__main__":
    _demo()
    print("ok")
