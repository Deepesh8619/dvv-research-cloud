#!/usr/bin/env python3
"""How many note clauses does the reader understand? Counts only, AI off.

    python3 scripts/note_census.py a.docx b.docx [--gold-dir DIR] [--examples]

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("documents", nargs="*", type=Path)
    ap.add_argument("--gold-dir", type=Path)
    ap.add_argument("--examples", action="store_true")
    args = ap.parse_args()
    docs = list(args.documents)
    if args.gold_dir:
        for gold in sorted(args.gold_dir.glob("*.json")):
            src = Path(json.loads(gold.read_text()).get("source_file") or "")
            if src.is_file() and src not in docs:
                docs.append(src)
    records: list[dict] = []
    for doc in docs:
        blocks = load_docx(str(doc))
        survey = extract_survey_definition(segment_into_questions(blocks), "census", blocks=blocks)
        records += read_notes(survey)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
