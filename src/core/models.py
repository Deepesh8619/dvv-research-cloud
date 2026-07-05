"""Canonical study model — the brain.

Every external format (SPSS, Triple-S, QSF, Decipher datamap) maps into these
structures; every engine computes over them; every export renders out of them.
No engine may depend on a source file format.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class VarType(StrEnum):
    SINGLE = "single"  # single-code categorical (one value from a list)
    MULTI = "multi"  # one dummy variable of a multi-response set
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"


@dataclass
class Variable:
    """One column of respondent data plus everything needed to interpret it."""

    name: str
    label: str = ""
    type: VarType = VarType.SINGLE
    values: dict[int, str] = field(default_factory=dict)  # code -> value label
    question_ref: str | None = None  # parent question (Q5 for Q5r2)
    question_text: str | None = None
    row: str | None = None  # grid row id
    col: str | None = None  # grid column id
    loop_context: str | None = None
    missing_values: list[int] = field(default_factory=list)
    provenance: str = "unknown"

    def valid_codes(self) -> list[int]:
        return [c for c in self.values if c not in self.missing_values]


@dataclass
class DataMap:
    """The variable dictionary for one study. Order matters (file order)."""

    variables: list[Variable] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index = {v.name: v for v in self.variables}

    def __getitem__(self, name: str) -> Variable:
        return self._index[name]

    def __contains__(self, name: str) -> bool:
        return name in self._index

    def __len__(self) -> int:
        return len(self.variables)

    def add(self, variable: Variable) -> None:
        if variable.name in self._index:
            raise ValueError(f"duplicate variable name: {variable.name}")
        self.variables.append(variable)
        self._index[variable.name] = variable

    def names(self) -> list[str]:
        return [v.name for v in self.variables]

    def to_json(self) -> str:
        return json.dumps([asdict(v) for v in self.variables], ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> DataMap:
        raw = json.loads(payload)
        variables = []
        for item in raw:
            item["type"] = VarType(item["type"])
            item["values"] = {int(k): v for k, v in item["values"].items()}
            variables.append(Variable(**item))
        return cls(variables=variables)
