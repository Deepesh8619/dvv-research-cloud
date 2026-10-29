"""Run the private gold set against this server's real projects.

    ./dvv gold gold/casual_dining.json            (from deploy/, the file stays on the server)
    python scripts/eval_gold.py cases.json --repeat 3 [--only <id or skill>] [--check]

--check validates the file and computes every fact without asking the model.
The score is added to Admin → Copilot answers as the "gold" suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", help="the case file, or - for standard input")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--only")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    from api.db import SessionLocal, UserDB, record_copilot_eval
    from src.ai.gold_eval import compute_facts, problems, resolve_study, run
    from src.utils.build_info import build_commit

    raw = sys.stdin.read() if args.cases == "-" else Path(args.cases).read_text()
    cases = json.loads(raw)["cases"]
    if found := problems(cases):
        print("The case file has problems:\n  " + "\n  ".join(found))
        return 2
    if args.only:
        cases = [c for c in cases if args.only in (c["id"], c.get("skill"))]
    db = SessionLocal()
    try:
        if args.check:
            for c in cases:
                study = resolve_study(db, c["study"])
                if study is None:
                    print(f"NO PROJECT  {c['id']}: nothing matches {c['study']}")
                    continue
                facts = compute_facts(study, c.get("facts") or {})
                print(f"ok  {c['id']:<34} {study.id}  " + ", ".join(
                    f"{k}={v[:40]}" for k, v in facts.items()))
            return 0
        # Not stored: an owner-level reader whose conversations are deleted after each case.
        user = UserDB(id="eval-gold", email="gold@eval", name="Gold eval", role="owner",
                      is_active=True)
        report = run(db, cases, user, repeat=max(1, args.repeat))
        s = report["summary"]
        print(f"\nGold score {s['score']}% ({s['passed']}/{s['cases']} passed every repeat) · "
              f"flaky: {', '.join(report['flaky']) or 'none'}")
        for skill, score in s["by_category"].items():
            print(f"  {skill:<14} {score}")
        if not args.only:
            record_copilot_eval(db, s, [r["id"] for r in report["results"] if not r["passed"]],
                                build_commit(), suite="gold", flaky=report["flaky"])
            print("Added to the score history (Admin → Copilot answers, gold).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":  # the heavy-work pool starts new processes that re-import this file
    sys.exit(main())
