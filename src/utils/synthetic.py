"""Synthetic study generator — used by benchmarks and demos until real
golden fixtures (anonymized client SPSS files) land in tests/golden/."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.weighting import rim_weight


def build_synthetic(rows: int, path: Path, verbose: bool = True) -> tuple[Path, DataMap]:
    """Generate a realistic synthetic study: demos (3 vars), 25 five-point
    scale questions with deliberate subgroup skews, and RIM weights."""
    rng = np.random.default_rng(2026)
    gender = rng.choice([1, 2], size=rows, p=[0.44, 0.56]).astype(np.int8)
    age = rng.choice([1, 2, 3, 4, 5, 6], size=rows).astype(np.int8)
    region = rng.choice([1, 2, 3, 4], size=rows).astype(np.int8)
    frame: dict = {"gender": gender, "age": age, "region": region}

    base_probs = np.array([0.18, 0.22, 0.25, 0.20, 0.15])
    for q in range(1, 26):
        # skew alternating questions by gender so demo tables show real sig letters
        shift = 0.06 if q % 2 else -0.06
        probs_m = base_probs + np.array([shift, shift / 2, 0, -shift / 2, -shift])
        probs_m /= probs_m.sum()
        answers = np.where(
            gender == 1,
            rng.choice([1, 2, 3, 4, 5], size=rows, p=probs_m),
            rng.choice([1, 2, 3, 4, 5], size=rows, p=base_probs),
        )
        frame[f"q{q}"] = answers.astype(np.int8)

    t0 = time.perf_counter()
    weights = rim_weight(
        pl.DataFrame({"gender": gender, "age": age}),
        {
            "gender": {1: 0.48, 2: 0.52},
            "age": {1: 0.16, 2: 0.18, 3: 0.18, 4: 0.17, 5: 0.16, 6: 0.15},
        },
    )
    rim_secs = time.perf_counter() - t0
    frame["weight"] = weights.weights

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(frame), str(path), compression="zstd")

    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable(
                "age", "Age group", VarType.SINGLE,
                {1: "18-24", 2: "25-34", 3: "35-44", 4: "45-54", 5: "55-64", 6: "65+"},
            ),
            Variable(
                "region", "Region", VarType.SINGLE,
                {1: "North", 2: "South", 3: "East", 4: "West"},
            ),
            *[
                Variable(
                    f"q{q}", f"Q{q}. Agreement statement {q}", VarType.SINGLE,
                    {
                        1: "Strongly agree", 2: "Agree", 3: "Neither",
                        4: "Disagree", 5: "Strongly disagree",
                    },
                )
                for q in range(1, 26)
            ],
            Variable("weight", "RIM weight", VarType.NUMERIC),
        ]
    )
    if verbose:
        print(
            f"  RIM weighting {rows:,} rows: {rim_secs:.2f}s "
            f"(efficiency {weights.efficiency:.1%}, {weights.iterations} iterations)"
        )
        print(f"  Parquet written: {path.stat().st_size / 1e6:.0f} MB "
              f"({rows:,} rows x {len(datamap)} vars)")
    return path, datamap
