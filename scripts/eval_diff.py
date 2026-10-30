"""Compare two reading-measurement rounds question by question.

    python scripts/eval_diff.py ROUND_BEFORE ROUND_AFTER [--rounds DIR]

A round is a folder eval_compare.py wrote (--out). Percentages hide trades —
ten questions fixed and three broken looks like progress — so this lists every
question that changed verdict. The rule for merging a reader change: "newly
wrong" is empty, or each entry is explained.

Exit code 1 when anything became wrong, so it can gate a script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(folder: Path) -> tuple[dict, dict]:
    verdicts, shape = {}, {}
    for f in sorted(folder.glob("*/results.json")):
        d = json.loads(f.read_text())
        shape[d["doc_id"]] = (d["gold_questions"], d["parser_questions"],
                              len(d.get("gold_only_ids") or []), len(d.get("extra") or []))
        for item in d["items"]:
            verdicts[(d["doc_id"], item["qid"])] = (item.get("layer1") or {}).get("verdict")
    return verdicts, shape


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--rounds", type=Path, default=Path.home() / "dev/DVV/eval/rounds")
    args = ap.parse_args(argv)
    old, old_shape = load(args.rounds / args.before)
    new, new_shape = load(args.rounds / args.after)

    def right(v):
        return v == "MATCH"

    wrong = sorted(k for k in old if right(old[k]) and k in new and not right(new[k]))
    fixed = sorted(k for k in new if right(new[k]) and not right(old.get(k)))
    print(f"right before {sum(map(right, old.values()))} of {len(old)}, "
          f"after {sum(map(right, new.values()))} of {len(new)}")
    print("newly wrong:", wrong or "none")
    print("newly right:", fixed or "none")
    print("\ndocument        gold/read/not found/extra  before -> after")
    for doc in sorted(new_shape):
        print(f"{doc:<16}{old_shape.get(doc)} -> {new_shape[doc]}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
