"""Benchmark the tab engine against the architecture §7.6 budgets.

Run: python scripts/benchmark_tabs.py [--rows 1000000]
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engines.tabs.crosstab import crosstab  # noqa: E402
from src.utils.synthetic import build_synthetic  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    args = parser.parse_args()

    print(f"Building synthetic study ({args.rows:,} respondents)...")
    t0 = time.perf_counter()
    path, datamap = build_synthetic(args.rows, ROOT / "data" / f"synthetic_{args.rows}.parquet")
    print(f"  Total build: {time.perf_counter() - t0:.2f}s\n")

    banner = ["gender", "age", "region"]

    t0 = time.perf_counter()
    crosstab(path, stub="q1", banner=banner, datamap=datamap, weight="weight")
    cold = time.perf_counter() - t0

    warm_times = []
    for q in ("q2", "q3", "q4", "q5"):
        t0 = time.perf_counter()
        crosstab(path, stub=q, banner=banner, datamap=datamap, weight="weight")
        warm_times.append(time.perf_counter() - t0)
    warm = sum(warm_times) / len(warm_times)

    t0 = time.perf_counter()
    for q in range(1, 26):
        crosstab(path, stub=f"q{q}", banner=banner, datamap=datamap, weight="weight")
    deck25 = time.perf_counter() - t0
    projected_500 = deck25 / 25 * 500

    print("Results vs architecture §7.6 budgets:")
    print(f"  Weighted sig-tested crosstab, cold:  {cold:.3f}s   (budget < 5s)   "
          f"{'PASS' if cold < 5 else 'FAIL'}")
    print(f"  Weighted sig-tested crosstab, warm:  {warm:.3f}s   (budget < 1s)   "
          f"{'PASS' if warm < 1 else 'FAIL'}")
    print(f"  25-table deck: {deck25:.1f}s -> projected 500-table deck: "
          f"{projected_500 / 60:.1f} min (budget < 5 min)   "
          f"{'PASS' if projected_500 < 300 else 'FAIL'}")


if __name__ == "__main__":
    main()
