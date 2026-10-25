"""The report checker: code, not a model, decides whether a sentence stands.

A sentence cites cells `{table, row, col}` (a table id, then indexes into that
table's rows and columns, as `runner.table_to_dict` shapes them). It stands only if
every number in it is a cited cell's percentage or base, or the gap between two
cited cells; every comparison has two cells behind it, "significant" has the sig
letter; and no cited column is too small to be more than indicative.
"""

from __future__ import annotations

import re

from src.engines.analysis.findings import MIN_BASE, checked_row, unmatched_numbers
from src.engines.tabs.intervals import differs

COMPARE = re.compile(r"\b(ahead of|more than|higher|lower|than|leads?|vs\.?|versus|outperform\w*"
                     r"|significant\w*)\b", re.I)
SIGNIFICANT = re.compile(r"\bsignificant", re.I)
RANKS = re.compile(r"\b(ahead of|more than|higher|lower|leads?|outperform\w*)\b", re.I)
SIMILAR = re.compile(r"\b(similar|in line with|level with|close to|no significant difference)\b",
                     re.I)
# Question ids (Q5, S1) and the "Base under 30" caution are not claims.
_NOT_CLAIMS = re.compile(r"\b[A-Za-z]+\d+\w*\b|\bbase under \d+", re.I)


def to_cites(found: list[dict], tables: list[dict]) -> list[dict]:
    """A finding's label-based citations as `{table, row, col}` indexes."""
    by_id = {t["id"]: t for t in tables}
    out = []
    for c in found:
        t = by_id.get(c["table"])
        if t is None:
            continue
        rows = [r["label"] for r in t["rows"]]
        cols = [x["label"] for x in t["columns"]]
        row = c["row"]
        if row not in rows and (hit := checked_row(t)) is not None:
            # A multi-select finding names the option ("Nescafé"); its own table
            # is one option, whose answer row is "Checked".
            row = hit["label"]
        if row in rows and c["column"] in cols:
            out.append({"table": c["table"], "row": rows.index(row),
                        "col": cols.index(c["column"])})
    return out


def check(text: str, cites: list[dict], tables: list[dict]) -> dict:
    by_id = {t["id"]: t for t in tables}
    problems: list[str] = []
    cells = []  # (table, row dict, col index, column dict)
    for c in cites:
        t = by_id.get(c.get("table"))
        try:
            if t is None or not (0 <= c["row"] < len(t["rows"])
                                 and 0 <= c["col"] < len(t["columns"])):
                raise IndexError
        except (IndexError, KeyError, TypeError):
            problems.append("A citation points at a cell that does not exist. "
                            "Remove it or pick another cell.")
            continue
        cells.append((t, t["rows"][c["row"]], c["col"], t["columns"][c["col"]]))

    pcts = [r["pct"][j] for _t, r, j, _c in cells]
    bases = [v for _t, _r, _j, col in cells for v in (col["base"], col["unweighted_base"])]
    gaps = [abs(a - b) for i, a in enumerate(pcts) for b in pcts[i + 1:]]
    bad = unmatched_numbers(_NOT_CLAIMS.sub(" ", text), [t for t, *_ in cells],
                            pcts, bases, [], gaps)
    if bad:
        problems.append(f"{', '.join(bad)} matches none of the cited cells. "
                        "Cite the cell it comes from or change the number.")

    if COMPARE.search(text) and len(cells) < 2:
        problems.append("This compares things but cites fewer than two cells. "
                        "Cite both cells or remove the comparison.")
    if SIGNIFICANT.search(text) and not _has_sig(cells):
        problems.append("It says significant, but no cited cell is marked significantly higher "
                        "than another. Cite the cells the test compares or drop the word.")

    if RANKS.search(text) and not SIMILAR.search(text):
        for i, a in enumerate(cells):
            for b in cells[i + 1:]:
                if _differ(a, b) is False and not _sig_between(a, b):
                    problems.append(
                        f"{a[1]['pct'][a[2]]:.0f}% and {b[1]['pct'][b[2]]:.0f}% are not reliably "
                        "different on these bases. Say they are close, or cite a tested "
                        "difference.")

    if "indicative" not in text.lower():
        for _t, _r, _j, col in cells:
            if col["unweighted_base"] < MIN_BASE:
                problems.append(f"Base under {MIN_BASE} for {col['label']} "
                                f"(n={col['unweighted_base']}): say it is indicative.")
    problems = list(dict.fromkeys(problems))
    return {"ok": not problems, "problems": problems}


def _has_sig(cells: list[tuple]) -> bool:
    """Some cited cell beats another cited cell of the same row, per its sig letters."""
    for t, r, j, _c in cells:
        beaten = (r.get("sig") or [""] * len(r["pct"]))[j] or ""
        for t2, r2, k, c2 in cells:
            if t2 is t and r2 is r and k != j and c2.get("letter") and c2["letter"] in beaten \
                    and r["pct"][j] > r["pct"][k]:
                return True
    return False


def _n(col: dict) -> float:
    return col.get("effective_base") or col["unweighted_base"]


def _differ(a: tuple, b: tuple) -> bool | None:
    """Two rows of one column share respondents; anything else is two groups."""
    (ta, ra, ja, ca), (tb, rb, jb, cb) = a, b
    if ta is tb and ja == jb:
        return differs(ra["pct"][ja], rb["pct"][jb], _n(ca))
    return differs(ra["pct"][ja], rb["pct"][jb], _n(ca), _n(cb))


def _sig_between(a: tuple, b: tuple) -> bool:
    """One cell is marked significantly higher than the other, in the same row."""
    return _has_sig([a, b]) or _has_sig([b, a])
