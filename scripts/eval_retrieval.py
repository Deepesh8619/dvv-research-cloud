"""Score search quality on evals/retrieval/cases.json.

    python scripts/eval_retrieval.py                 # whichever embedder is available
    python scripts/eval_retrieval.py --out run.json

The embedder is the local model when Ollama answers (DVV_EMBED_MODEL, default
nomic-embed-text), otherwise the lexical fallback — the report says which.
No client data: the cases are synthetic.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engines.rag.retrieval_eval import MODES, evaluate, load  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="where to write the JSON report")
    parser.add_argument("--misses", action="store_true", help="list the searches hybrid missed")
    args = parser.parse_args()
    report = evaluate(load(ROOT / "evals" / "retrieval" / "cases.json"))
    print(f"Embedder: {report['embedder']} · {report['queries']} searches\n")
    print(f"{'':<12}" + "".join(f"{m:>22}" for m in MODES))
    print(f"{'':<12}" + "".join(f"{'R@1   R@3   MRR':>22}" for _ in MODES))
    rows = [("overall", report["overall"]),
            *([("held-out", report["held_out"])] if report["held_out"] else []),
            *report["by_type"].items()]
    for name, scores in rows:
        cells = "".join(f"{s['recall@1']:>8.2f}{s['recall@3']:>6.2f}{s['mrr']:>6.2f}  "
                        for s in (scores[m] for m in MODES))
        print(f"{name:<12}{cells}")
    if args.misses:
        print("\nHybrid missed (not in the top 3):")
        for r in report["per_query"]:
            if not (r["hybrid+glossary"]["first"] and r["hybrid+glossary"]["first"] <= 3):
                got = r["hybrid+glossary"]["top3"]
                print(f"  [{r['type']}] {r['q']!r}: expected {r['expect']}, got {got}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
