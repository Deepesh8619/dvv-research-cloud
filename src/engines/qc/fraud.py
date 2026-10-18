"""Fraud signals: reasons to LOOK at a respondent, never a verdict.

A shared connection is also a household, an office or a campus; a missing
user agent is also a privacy tool. The score is just the signal count and must
never auto-remove anyone. Pure and deterministic over (DataFrame, DataMap).

Signals (paradata column names come from fielding/materialize.py):
- shared_connection: same non-null vConnection on 2+ rows.
- copied_open_end: identical normalised TEXT answer of >= 20 characters across
  different respondents. The 20 is MEASURED: 0 hits on 1551 real rows at 20,
  19 stock-phrase hits at 10. Do not lower it.
- no_user_agent: vDevice == 99.
- place_mismatch: vPlaceCheck == 2 (informational).

A signal whose column is absent (uploaded files have no paradata) is listed in
`checks_skipped` instead of guessed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable

import polars as pl

from src.core.models import DataMap, VarType
from src.engines.qc.variable_roles import classify_variable

MIN_COPIED_CHARS = 20
_ANSWER_ROLES = ("question", "other_specify")


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.casefold()).strip()


def fraud_signals(
    df: pl.DataFrame, dm: DataMap, key_of: Callable[[int, dict], str] | None = None
) -> dict:
    """Return {"flags": {key: {"signals": [...], "score": n}}, "checks_skipped": [...]}.

    key_of(i, row_dict) -> respondent key; default "row:{i}".
    """
    key_of = key_of or (lambda i, _row: f"row:{i}")
    rows = df.to_dicts()
    hits: dict[int, list[str]] = defaultdict(list)
    skipped: list[str] = []

    def groups(values) -> list[list[int]]:
        g: dict[str, list[int]] = defaultdict(list)
        for i, v in enumerate(values):
            if v is not None and v != "":
                g[v].append(i)
        return [ix for ix in g.values() if len(ix) > 1]

    if "vConnection" in df.columns:
        for ix in groups(r["vConnection"] for r in rows):
            for i in ix:
                hits[i].append("shared_connection")
    else:
        skipped.append("shared_connection")

    # paradata TEXT columns (country, city...) are not open-ends, and neither
    # are an export's own system columns (user agent, the script's variable
    # lists): on a real Decipher export those flagged every complete.
    texts = [
        v.name for v in dm.variables
        if v.type == VarType.TEXT and v.provenance != "fielding_paradata" and v.name in df.columns
        and classify_variable(v.name, v.label or "", v.question_ref) in _ANSWER_ROLES
    ]
    if texts:
        copied: set[int] = set()
        for name in texts:
            vals = [
                n if isinstance(r[name], str) and len(n := _norm(r[name])) >= MIN_COPIED_CHARS
                else None
                for r in rows
            ]
            # One text shared by most respondents is a constant the script wrote,
            # not an answer anybody copied.
            answered = sum(1 for v in vals if v)
            for ix in groups(vals):
                if answered < 10 or len(ix) * 2 <= answered:
                    copied.update(ix)
        for i in copied:
            hits[i].append("copied_open_end")
    else:
        skipped.append("copied_open_end")

    for col, val, sig in (("vDevice", 99, "no_user_agent"), ("vPlaceCheck", 2, "place_mismatch")):
        if col in df.columns:
            for i, r in enumerate(rows):
                if r[col] == val:
                    hits[i].append(sig)
        else:
            skipped.append(sig)

    flags = {
        key_of(i, rows[i]): {"signals": s, "score": len(s)} for i, s in sorted(hits.items())
    }
    return {"flags": flags, "checks_skipped": skipped}
