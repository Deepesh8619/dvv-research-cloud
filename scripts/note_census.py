#!/usr/bin/env python3
"""How many note clauses does the reader understand? Counts only, AI off.

    python3 scripts/note_census.py a.docx b.docx [--gold-dir DIR] [--examples]
        [--notes-gold FILE [--max-false-applied N]]

--gold-dir adds the document each gold json names in `source_file` (missing ones
are skipped). --examples prints the unread clauses; keep that output out of any
file. Writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

for key in ("BEDROCK_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN"):
    os.environ[key] = ""
os.environ["OLLAMA_HOST"] = "http://127.0.0.1:1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engines.questionnaire.extractor import extract_survey_definition  # noqa: E402
from src.engines.questionnaire.note_ledger import OUTCOMES, read_notes  # noqa: E402
from src.intake.questionnaire_loader import load_docx, segment_into_questions  # noqa: E402


def gold_report(records: list[dict], gold_file: Path, max_false: int | None) -> int:
    """Agreement with hand-read outcomes, numbers only. Non-zero if too many false applied."""
    gold = json.loads(gold_file.read_text())
    ours: dict[tuple, str] = {}
    for r in records:
        ours.setdefault((r.get("doc"), r["qid"], r["said"]), r["outcome"])
    agree: Counter = Counter()
    seen: Counter = Counter()
    false_applied = missing = 0
    for g in gold:
        got = ours.get((g["doc"], g["qid"], g["said"]))
        missing += got is None
        seen[g["outcome"]] += 1
        agree[g["outcome"]] += got == g["outcome"]
        false_applied += got == "applied" and g["outcome"] != "applied"
    print(f"gold {len(gold)} clauses, agree {sum(agree.values())} "
          f"({100 * sum(agree.values()) // max(len(gold), 1)}%), not found {missing}")
    for o in OUTCOMES:
        if seen[o]:
            print(f"  gold {o:<10}{agree[o]:>5} / {seen[o]}")
    print(f"false applied {false_applied}")
    if max_false is not None and false_applied > max_false:
        print(f"FAIL: false applied {false_applied} > {max_false}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("documents", nargs="*", type=Path)
    ap.add_argument("--gold-dir", type=Path)
    ap.add_argument("--examples", action="store_true")
    ap.add_argument("--notes-gold", type=Path, help="JSON list of {doc, qid, said, outcome}")
    ap.add_argument("--max-false-applied", type=int, default=None)
    args = ap.parse_args()
    docs = list(args.documents)
    labels = {d: d.stem for d in docs}
    if args.gold_dir:
        for gold in sorted(args.gold_dir.glob("*.json")):
            src = Path(json.loads(gold.read_text()).get("source_file") or "")
            if src.is_file() and src not in docs:
                docs.append(src)
                labels[src] = gold.stem
    records: list[dict] = []
    for doc in docs:
        blocks = load_docx(str(doc))
        survey = extract_survey_definition(segment_into_questions(blocks), "census", blocks=blocks)
        records += [{**r, "doc": labels[doc]} for r in read_notes(survey)]
    table: dict[str, Counter] = {}
    for r in records:
        table.setdefault(r["kind"], Counter())[r["outcome"]] += 1
    print(f"{len(docs)} documents, {len(records)} records")
    print(f"{'kind':<16}" + "".join(f"{o:>10}" for o in OUTCOMES) + f"{'total':>8}")
    for kind, c in sorted(table.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{kind:<16}" + "".join(f"{c[o]:>10}" for o in OUTCOMES) + f"{sum(c.values()):>8}")
    tot = Counter(r["outcome"] for r in records)
    print(f"{'TOTAL':<16}" + "".join(f"{tot[o]:>10}" for o in OUTCOMES) + f"{len(records):>8}")
    if args.examples:
        for r in records:
            if r["outcome"] == "unread":
                print(f"[{r['kind']}] {r['qid']}: {r['said']}")
    if args.notes_gold:
        return gold_report(records, args.notes_gold, args.max_false_applied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
