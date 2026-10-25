"""Person-fit: does one respondent's grid pattern fit the order everyone else gives?

Speeders and straight-liners are caught elsewhere; a bot or an inattentive
respondent can give varied answers that still make no sense against the crowd.

**Normalised Guttman errors G\\*** (Emons 2008, polytomous). Each item with m+1
answer levels has m *item steps*: step k is "scored at least k". A step is
"easier" the more respondents pass it. Order every step of the grid from most
to least passed (ties keep item order). A respondent's steps then form a 0/1
string; in a perfect Guttman pattern all the 1s come first. A **Guttman error**
is a pair (easier step failed, harder step passed). With total score r (number
of 1s) over S steps, the most errors possible is r*(S-r) (every 0 before every
1), so

    G* = errors / (r * (S - r)),   in [0, 1]; 0 when r = 0 or r = S.

Worked example (tests/unit/test_person_fit.py): items A, B on a 0-2 scale, five
respondents (A,B) = (2,2) (2,1) (1,1) (0,0) (0,2). Passed shares give the step
order B>=1, A>=1, A>=2, B>=2. Respondent (0,2) is 1,0,0,1: two zeros precede the
last 1, so errors = 2, r = 2, S = 4, G* = 2/4 = 0.5; all others are 0.

r*(S-r) ignores that a step can't be passed without the one below it, so G* is
a slightly conservative (low) ratio; ranking within a grid is what we use.

**IRV** (intra-individual response variability) is the SD across the grid's
items. IRV 0 is a straight-liner, already flagged by `straightliner`, so such
rows are never also called misfits.

A respondent is flagged when they are in the top 5% of G* on at least two
grids (or on the only grid there is). Needs 30+ people who fully answered the
grid; grids under 6 items are skipped.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from src.core.models import DataMap
from src.engines.qc.respondents import _GRID_ITEM_RE, _grid_groups

MIN_ITEMS, MIN_RESPONDENTS, TOP_SHARE = 6, 30, 0.05
# Top 5% alone is relative: a clean study would always lose 5% of its respondents.
# A misfit must also clear an absolute G* (0.30, a common cut-off in the
# person-fit literature), so a study where everyone fits flags nobody.
MIN_G = 0.30


def g_star(ranks: np.ndarray, levels: int) -> np.ndarray:
    """G* per row of `ranks` (respondents x items, values 0..levels-1)."""
    m = levels - 1
    steps = np.concatenate([(ranks[:, [j]] >= np.arange(1, m + 1)) for j in range(ranks.shape[1])],
                           axis=1).astype(int)
    order = np.argsort(-steps.mean(axis=0), kind="stable")
    z = steps[:, order]
    errors = ((np.cumsum(1 - z, axis=1)) * z).sum(axis=1)
    r, s = z.sum(axis=1), z.shape[1]
    top = r * (s - r)
    return np.where(top > 0, errors / np.maximum(top, 1), 0.0)


def _grid_name(df_cols: list[str], datamap: DataMap) -> str:
    v = next(v for v in datamap.variables if v.name == df_cols[0])
    if v.question_ref:
        return v.question_ref
    m = _GRID_ITEM_RE.match(v.name)
    return m.group(1) if m and m.group(1) else v.name


def person_fit_flags(df: pl.DataFrame, datamap: DataMap) -> dict[int, list[tuple[str, str]]]:
    """{row index: [("person_fit", plain-words detail)]} for misfitting respondents."""
    by_name = {v.name: v for v in datamap.variables}
    top_on: dict[int, list[str]] = {}
    graded = 0
    for grid in _grid_groups(datamap):
        cols = [c for c in grid if c in df.columns]
        if len(cols) < MIN_ITEMS:
            continue
        codes = sorted(float(c) for c in by_name[cols[0]].valid_codes())
        if len(codes) < 3:  # a yes/no grid has one step per item; nothing to order
            continue
        raw = df.select([pl.col(c).cast(pl.Float64, strict=False) for c in cols]).to_numpy()
        ranks = np.searchsorted(codes, raw)  # off-scale codes (DK, 99) read as unanswered
        valid = ~np.isnan(raw) & (np.take(codes, np.minimum(ranks, len(codes) - 1)) == raw)
        full = valid.all(axis=1)
        if full.sum() < MIN_RESPONDENTS:
            continue
        graded += 1
        idx = np.flatnonzero(full)
        g = g_star(ranks[idx], len(codes))
        cut = np.quantile(g, 1 - TOP_SHARE)
        varied = ranks[idx].std(axis=1) > 0  # IRV 0 is a straight-liner, not a misfit
        name = _grid_name(cols, datamap)
        for i in idx[(g >= max(cut, MIN_G)) & varied]:
            top_on.setdefault(int(i), []).append(name)
    need = 2 if graded > 1 else 1
    out = {}
    for i, names in top_on.items():
        if len(names) >= need:
            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            out[i] = [("person_fit", f"On {joined}, answers contradict the order everyone "
                                     "else gives these items (top 5%).")]
    return out
