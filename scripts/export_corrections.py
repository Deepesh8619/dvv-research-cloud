"""Write the saved question corrections to a JSONL file for training. Owner, on the server.

    PYTHONPATH=. python scripts/export_corrections.py --out FILE [--mask "word,word"]

Reads the database directly; there is no API route for this on purpose, so client
text never leaves through the web. Only projects whose owner has allowed training
and that are not deleted are included. Each line: {"input": source_text,
"output": after, "before": before}.

Masking replaces the project's name and the --mask terms, case-insensitively, as
whole words, everywhere in the row. It is by listed terms ONLY: anything not
listed goes out as written. A person must review the file before it leaves the
server. Prints counts only, never text.
"""

import argparse
import json
import re

from api.db import ReadingCorrectionDB, SessionLocal, StudyDB

MASK = "[MASKED]"


def _masker(terms: list[str]):
    terms = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    if not terms:
        return lambda text: (text, 0)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)",
                         re.IGNORECASE)
    return lambda text: pattern.subn(MASK, text)


def export(db, out: str, extra_terms: list[str]) -> dict:
    studies = {s.id: s for s in db.query(StudyDB).filter(StudyDB.status != "archived")}
    allowed = {i for i, s in studies.items()
               if (s.study_metadata or {}).get("corrections_for_training")}
    rows = db.query(ReadingCorrectionDB).all()
    written = masked = 0
    with open(out, "w") as f:
        for r in rows:
            if r.study_id not in allowed:
                continue
            mask = _masker([studies[r.study_id].client_name or "", *extra_terms])
            line, n = mask(json.dumps({"input": r.source_text, "output": json.loads(r.after),
                                       "before": json.loads(r.before)}, ensure_ascii=False))
            f.write(line + "\n")
            written += 1
            masked += n
    return {"rows_written": written, "rows_skipped": len(rows) - written,
            "terms_masked": masked}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mask", default="")
    args = ap.parse_args()
    with SessionLocal() as session:
        print(export(session, args.out, args.mask.split(",")))
