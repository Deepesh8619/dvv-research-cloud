"""Tab plan specification: the banner/stub recipe a DP analyst writes (or the
AI drafts from a client's tab spec document).

YAML example:

    title: Wave 12 tracker
    weight: weight
    confidence: 0.95
    banner: [gender, age]
    tables:
      - stub: q1
        nets: auto            # T2B/B2B inferred from the scale
        mean: true
      - stub: q2
        nets:
          - label: "NET: Aware"
            codes: [1, 2]
        filter: {region: [1, 2]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.core.models import DataMap, Variable
from src.engines.tabs.crosstab import Net


@dataclass
class TableSpec:
    stub: str
    nets: list[Net] = field(default_factory=list)
    mean: bool = False
    filter: dict[str, list[int]] | None = None


@dataclass
class TabPlan:
    title: str
    banner: list[str]
    tables: list[TableSpec]
    weight: str | None = None
    confidence: float = 0.95
    min_base_for_test: int = 30


def auto_nets(variable: Variable) -> list[Net]:
    """Infer Top-2-Box / Bottom-2-Box nets for 5+ point ordered scales.

    Assumes ascending codes where the lowest codes are the top of the scale
    (the dominant MR convention: 1 = most positive). Override in the spec
    when a scale runs the other way.
    """
    codes = sorted(variable.valid_codes())
    if len(codes) < 5:
        return []
    top, bottom = codes[:2], codes[-2:]

    def _label(prefix: str, members: list[int]) -> str:
        labels = [variable.values.get(c, str(c)) for c in members]
        return f"{prefix} ({' + '.join(labels)})"

    return [
        Net(label=_label("Top 2 Box", top), codes=top),
        Net(label=_label("Bottom 2 Box", bottom), codes=bottom),
    ]


def load_tab_plan(path: str | Path, datamap: DataMap) -> TabPlan:
    """Parse and validate a YAML tab plan against the study's DataMap."""
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    banner = raw.get("banner", [])
    if not banner:
        raise ValueError("tab plan needs a non-empty 'banner' list")
    for var in banner:
        if var not in datamap:
            raise ValueError(f"banner variable not in datamap: {var}")

    weight = raw.get("weight")
    if weight and weight not in datamap:
        raise ValueError(f"weight variable not in datamap: {weight}")

    tables: list[TableSpec] = []
    for entry in raw.get("tables", []):
        stub = entry["stub"]
        if stub not in datamap:
            raise ValueError(f"stub variable not in datamap: {stub}")
        nets_raw = entry.get("nets")
        if nets_raw == "auto":
            nets = auto_nets(datamap[stub])
        elif nets_raw:
            nets = [Net(label=n["label"], codes=list(n["codes"])) for n in nets_raw]
        else:
            nets = []
        filter_ = entry.get("filter")
        if filter_:
            filter_ = {var: [int(c) for c in codes] for var, codes in filter_.items()}
        tables.append(TableSpec(stub=stub, nets=nets, mean=bool(entry.get("mean")), filter=filter_))

    if not tables:
        raise ValueError("tab plan has no tables")

    return TabPlan(
        title=raw.get("title", "Untitled tab plan"),
        banner=list(banner),
        tables=tables,
        weight=weight,
        confidence=float(raw.get("confidence", 0.95)),
        min_base_for_test=int(raw.get("min_base_for_test", 30)),
    )
