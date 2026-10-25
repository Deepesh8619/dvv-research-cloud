"""Person-fit (G*): hand-computed example, then flagging behaviour on synthetic grids."""

import numpy as np
import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.person_fit import g_star, person_fit_flags


def test_g_star_matches_the_hand_computed_example():
    # Items A, B on 0-2; respondents (A,B). Step order B>=1, A>=1, A>=2, B>=2.
    ranks = np.array([[2, 2], [2, 1], [1, 1], [0, 0], [0, 2]])
    assert g_star(ranks, 3).tolist() == [0, 0, 0, 0, 0.5]


def _grids(names_n: dict[str, int], rows: dict[str, list[int]]) -> tuple[pl.DataFrame, DataMap]:
    vs, data = [], {}
    for g, n in names_n.items():
        for j in range(1, n + 1):
            vs.append(Variable(name=f"{g}r{j}", type=VarType.SINGLE, question_ref=g,
                               values={c: str(c) for c in range(1, 6)}))
            data[f"{g}r{j}"] = [row[j - 1] for row in rows[g]]
    return pl.DataFrame(data), DataMap(variables=vs)


def _population(n=100, items=8, seed=1):
    rng = np.random.default_rng(seed)
    level = np.linspace(1, 5, items)  # item i is "harder" the further along
    sub = np.clip(np.round(level[::-1] + rng.normal(0, 0.4, (n, items))), 1, 5).astype(int)
    return sub.tolist()


def _with_reversed(rows, k=5):
    rows = [r[:] for r in rows]
    for i in range(k):
        rows[i] = sorted(rows[i])  # ascending vs the common descending order, but varied
    return rows


def test_reverse_pattern_respondents_are_flagged_and_consistent_ones_are_not():
    base = _population()
    rows = _with_reversed(base)
    df, dm = _grids({"SAT_ATTR": 8, "BRAND_IMG": 8}, {"SAT_ATTR": rows, "BRAND_IMG": rows})
    found = person_fit_flags(df, dm)
    assert {0, 1, 2, 3, 4} <= set(found)
    assert not set(range(5, 100)) & set(found)
    assert "SAT_ATTR and BRAND_IMG" in found[0][0][1] and "top 5%" in found[0][0][1]


def test_one_grid_is_enough_when_it_is_the_only_one_and_two_are_needed_otherwise():
    base = _population()
    bad = _with_reversed(base)
    df, dm = _grids({"A": 8, "B": 8}, {"A": bad, "B": base})
    assert person_fit_flags(df, dm) == {}  # misfit on one of two grids: no flag
    df, dm = _grids({"A": 8}, {"A": bad})
    assert set(person_fit_flags(df, dm)) >= {0, 1, 2, 3, 4}


def test_under_30_respondents_gives_no_flag():
    rows = _with_reversed(_population(n=29))
    df, dm = _grids({"A": 8}, {"A": rows})
    assert person_fit_flags(df, dm) == {}


def test_straightliners_are_not_called_misfits():
    rows = _with_reversed(_population())
    rows[10] = [3] * 8
    rows[11] = [5] * 8
    df, dm = _grids({"A": 8}, {"A": rows})
    assert not {10, 11} & set(person_fit_flags(df, dm))


def test_a_study_where_everyone_fits_flags_nobody():
    """Top 5% is relative; the absolute G* floor keeps a clean study clean."""
    rows = _population(n=200, seed=3)
    df, dm = _grids({"SAT_ATTR": 8}, {"SAT_ATTR": rows})
    assert person_fit_flags(df, dm) == {}
