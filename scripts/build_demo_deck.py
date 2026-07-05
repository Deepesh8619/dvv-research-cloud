"""End-to-end demo: synthetic study -> tab plan -> formatted Excel deck.

Run: python scripts/build_demo_deck.py [--rows 1000000]
Output: data/demo_deck.xlsx
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engines.tabs.runner import run_tab_plan  # noqa: E402
from src.engines.tabs.spec import load_tab_plan  # noqa: E402
from src.renderers.excel import render_deck  # noqa: E402
from src.utils.synthetic import build_synthetic  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    args = parser.parse_args()

    parquet = ROOT / "data" / f"synthetic_{args.rows}.parquet"
    print(f"Building synthetic study ({args.rows:,} respondents)...")
    parquet, datamap = build_synthetic(args.rows, parquet)

    plan = load_tab_plan(ROOT / "specs" / "demo_tabplan.yaml", datamap)
    print(f"\nRunning tab plan '{plan.title}' ({len(plan.tables)} tables)...")
    t0 = time.perf_counter()
    run = run_tab_plan(parquet, plan, datamap)
    compute_secs = time.perf_counter() - t0

    t0 = time.perf_counter()
    out = render_deck(run, ROOT / "data" / "demo_deck.xlsx")
    render_secs = time.perf_counter() - t0

    print(f"  Tables computed: {compute_secs:.2f}s | Excel rendered: {render_secs:.2f}s")
    print(f"  Deck written to: {out}")


if __name__ == "__main__":
    main()
