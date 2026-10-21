"""Several waves or countries of one tracker, checked against each other and stacked.

A tracker is the same questionnaire fielded again and again — each wave, each
country, its own data file. Before anyone trends or pools them, the question is
what drifted: a question dropped in one country, a code added in wave 3, a code
whose label changed (code 3 was "Brand C", now "Brand D" — the classic tracker
error, a relabelling read as a movement). `drift` names each one against the
first source; `stack` puts the files in one, with a SOURCE variable saying
which file each respondent came from.
"""

from __future__ import annotations

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.variable_roles import classify_variable
from src.engines.tabs.tracking import _norm, relabelled

SOURCE = "SOURCE"


def _differences(ref: Variable, v: Variable, ref_name: str) -> list[str]:
    if ref.type != v.type:
        return [f"type {ref.type.value} in {ref_name}, {v.type.value} here"]
    moved = relabelled(ref.values, v.values)
    shared = set(ref.values) & set(v.values)
    # The same answers under other numbers: "Sago" was 3, now 4.
    where = {_norm(label): code for code, label in ref.values.items()}
    renumbered = [(c, where[_norm(v.values[c])]) for c in moved if _norm(v.values[c]) in where]
    if renumbered and len(renumbered) * 2 >= len(moved):
        examples = "; ".join(f"{v.values[c]!r} is {c} here, {d} in {ref_name}"
                             for c, d in renumbered[:2])
        return [f"codes renumbered ({len(renumbered)} answers moved): {examples}"]
    if shared and len(moved) * 2 > len(shared):
        return [f"a different question under the same name: {len(moved)} of {len(shared)} "
                f"codes mean something else (code {moved[0]} is {v.values[moved[0]]!r} here, "
                f"{ref.values[moved[0]]!r} in {ref_name})"]
    out = [f"code {c} is {v.values[c]!r} here, {ref.values[c]!r} in {ref_name}" for c in moved]
    if added := sorted(set(v.values) - set(ref.values)):
        out.append(f"codes added: {added}")
    if dropped := sorted(set(ref.values) - set(v.values)):
        out.append(f"codes missing: {dropped}")
    return out


def drift(sources: list[tuple[str, DataMap]]) -> dict:
    """What differs between the sources' data maps, against the first one."""
    names = [n for n, _ in sources]
    order: list[str] = []
    holders: dict[str, list[str]] = {}
    for name, dm in sources:
        for v in dm.variables:
            if v.name not in holders:
                order.append(v.name)
            holders.setdefault(v.name, []).append(name)
    ref_name, ref = sources[0]
    changed = []
    for name, dm in sources[1:]:
        for v in dm.variables:
            if v.name in ref and (problems := _differences(ref[v.name], v, ref_name)):
                kind = classify_variable(v.name, v.label or "", v.question_ref)
                # Added or missing codes stack fine; a code whose meaning moved does not.
                breaking = any(not p.startswith(("codes added", "codes missing"))
                               for p in problems)
                changed.append({"variable": v.name, "label": (v.label or "")[:120], "source": name,
                                "script": kind == "system", "breaking": breaking,
                                "problems": problems})
    # Breaking questions first, script columns last.
    changed.sort(key=lambda c: (c["script"], not c["breaking"]))
    partial = [{"variable": v, "in": holders[v]} for v in order if len(holders[v]) < len(names)]
    n_breaking = sum(c["breaking"] and not c["script"] for c in changed)
    return {"sources": names, "n_variables": len(order), "n_breaking": n_breaking,
            "n_in_every_source": len(order) - len(partial),
            "not_in_every_source": partial, "changed": changed}


def stack(parts: list[tuple[str, pl.DataFrame, DataMap]]) -> tuple[pl.DataFrame, DataMap]:
    """One file from many, SOURCE first; a variable missing from a source is blank there."""
    frames = [df.with_columns(pl.lit(i, dtype=pl.Int64).alias(SOURCE))
              for i, (_n, df, _dm) in enumerate(parts, start=1)]
    data = pl.concat(frames, how="diagonal_relaxed")
    dm = DataMap([Variable(name=SOURCE, label="Which file (wave or country) the respondent is from",
                           type=VarType.SINGLE,
                           values={i: n for i, (n, _df, _dm) in enumerate(parts, start=1)})])
    for _n, _df, part in parts:
        for v in part.variables:
            if v.name == SOURCE:
                continue
            if v.name in dm:
                # A code another source added keeps that source's label.
                dm[v.name].values = {**v.values, **dm[v.name].values}
            else:
                dm.add(Variable(**{**v.__dict__, "values": dict(v.values)}))
    return data.select([SOURCE, *[c for c in data.columns if c != SOURCE]]), dm
