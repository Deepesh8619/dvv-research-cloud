"""Typing and timing evidence from export trace columns (Decipher naming).

variable_roles files these columns under "system" so tables skip them; here they
are read back as evidence of whether a person typed, pasted or spoke an answer.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from src.engines.qc.cleaning import respondent_keys

_KINDS = {"keystrokecount": "keystrokes", "charactercount": "chars", "copyresponse": "paste",
          "copy_paste_flagged": "paste", "speechtotexttext": "stt", "manualtyping": "manual"}
_TRACE_RE = re.compile(
    r"^(?P<base>.+?)(?:OE)?_?(?P<kind>" + "|".join(_KINDS) + r")$", re.I)
_FALSY = {"", "0", "0.0", "false", "no", "none", "nan", "null"}
PASTE_MIN_CHARS = 20
PASTE_KEY_RATIO = 0.3  # fewer keystrokes than this share of the characters = pasted


def paradata_columns(columns: list[str]) -> dict[str, dict[str, str]]:
    """{"Q15": {"keystrokes": col, "chars": col, "paste": col, "stt": col, "manual": col}}."""
    out: dict[str, dict[str, str]] = {}
    for c in columns:
        m = _TRACE_RE.match(c)
        if not m:
            continue
        base = m["base"]
        if len(base) > 1 and base[0] == "h" and not base[1].islower():
            base = base[1:]  # Decipher's hidden-variable prefix
        out.setdefault(base, {})[_KINDS[m["kind"].lower()]] = c
    return out


def _truthy(v: Any) -> bool:
    return v is not None and str(v).strip().lower() not in _FALSY


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _row_features(record: dict, columns_map: dict[str, dict[str, str]]) -> dict:
    traced, pasted, speech, ratios = 0, [], [], []
    for base, cols in columns_map.items():
        vals = {k: record.get(c) for k, c in cols.items()}
        if all(v is None for v in vals.values()):
            continue
        traced += 1
        keys, chars = _num(vals.get("keystrokes")), _num(vals.get("chars"))
        talked = _truthy(vals.get("stt"))
        if talked:
            speech.append(base)
        if _truthy(vals.get("paste")) or (
                not talked and keys is not None and chars is not None
                and chars >= PASTE_MIN_CHARS and keys < chars * PASTE_KEY_RATIO):
            pasted.append(base)
        if chars is not None and keys is not None:
            ratios.append(chars / max(keys, 1))
    return {"n_open_ends_traced": traced, "pasted": pasted, "speech": speech,
            "max_chars_per_key": max(ratios) if ratios else None}


def paradata_features(df: pl.DataFrame, columns_map: dict[str, dict[str, str]]) -> pl.DataFrame:
    """One row per respondent, keyed as the cleaning engine keys them."""
    used = sorted({c for cols in columns_map.values() for c in cols.values()})
    rows = [_row_features(r, columns_map) for r in df.select(used).iter_rows(named=True)]
    return pl.DataFrame({
        "respondent_id": respondent_keys(df),
        "n_open_ends_traced": [r["n_open_ends_traced"] for r in rows],
        "n_pasted": [len(r["pasted"]) for r in rows],
        "n_speech": [len(r["speech"]) for r in rows],
        "max_chars_per_key": pl.Series([r["max_chars_per_key"] for r in rows], dtype=pl.Float64),
        "pasted_qids": [",".join(r["pasted"]) for r in rows],
    }, schema_overrides={"n_open_ends_traced": pl.Int64, "n_pasted": pl.Int64,
                         "n_speech": pl.Int64})


def row_paradata(df: pl.DataFrame, row: int) -> dict | None:
    """The evidence block for one respondent, or None when the file has no traces."""
    cmap = paradata_columns(df.columns)
    if not cmap:
        return None
    f = _row_features(df.row(row, named=True), cmap)
    return {"pasted": f["pasted"], "speech": f["speech"],
            "max_chars_per_key": f["max_chars_per_key"]}
