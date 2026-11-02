"""C1: what a person settled on for a question, versus what the reader said.

One row per study + qid. The first edit keeps the reader's answer as `before`;
later edits only replace `after`. Undoing every change deletes the row. This is
the clean, verified record that prompt examples (C2) and any training draw on.
Not the same thing as `CorrectionDB`, which holds a person's plain-language lessons.
"""

import json
import uuid
from datetime import UTC, datetime

from api.db import ReadingCorrectionDB, StudyDB

# Validation keys that record how the reader worked, not what it decided.
INTERNAL_VALIDATION_KEYS = frozenset({
    "resolution", "extraction_error", "verification_error", "verification_checks",
    "routing_verbatim", "scripter_directives", "unresolved_fields", "unknown_answer_tags",
    "unrecognised_rows", "lifted_notes", "extracted_by", "brief_rules", "original_qid",
    "notes", "intended_type", "not_in_link",
})


def _option(o) -> dict:
    return {"code": o.code, "label": o.label, "exclusive": o.exclusive, "anchored": o.anchored}


def reading_of(question) -> dict:
    """Only what a reader decides. Fingerprints, flags and provenance stay out."""
    return {
        "text": question.text,
        "type": question.type.value,
        "options": [_option(o) for o in question.options],
        "rows": [_option(o) for o in question.rows] if question.rows is not None else None,
        "cols": [_option(o) for o in question.cols] if question.cols is not None else None,
        "required": question.required,
        "randomize_options": question.randomize_options,
        "show_if": question.show_if.to_dict() if question.show_if else None,
        "terminate_if": question.terminate_if.to_dict() if question.terminate_if else None,
        "validation": {k: v for k, v in (question.validation or {}).items()
                       if k not in INTERNAL_VALIDATION_KEYS} or None,
    }


def changed_fields(before: dict, after: dict) -> list[str]:
    return [k for k in after if before.get(k) != after[k]]


def record(db, study_id: str, qid: str, before: dict, after: dict, source_text,
           by_id: str, how: str = "edit", old_qid: str | None = None) -> None:
    """Save, update or (when the fix was undone) delete this question's row.

    `old_qid` is the qid the question had before a rename, so its row moves."""
    row = db.query(ReadingCorrectionDB).filter_by(study_id=study_id, qid=old_qid or qid).first()
    if row is not None:
        before = json.loads(row.before)  # the reader's first answer stays
    if before == after:
        if row is not None:
            db.delete(row)
            db.commit()
        return
    now = datetime.now(UTC).isoformat()
    if row is None:
        row = ReadingCorrectionDB(id=f"rc_{uuid.uuid4().hex[:12]}", study_id=study_id,
                                  created_at=now, before=json.dumps(before))
        db.add(row)
    row.qid = qid
    row.source_text = source_text
    row.after = json.dumps(after)
    row.changed_fields = json.dumps(changed_fields(before, after))
    row.by_id = by_id
    row.how = how
    row.updated_at = now
    db.commit()


def rows_for_examples(db, limit: int = 50) -> list[ReadingCorrectionDB]:
    """Newest first, leaving out projects that have been deleted (C2)."""
    return (db.query(ReadingCorrectionDB)
            .join(StudyDB, StudyDB.id == ReadingCorrectionDB.study_id)
            .filter(StudyDB.status != "archived")
            .order_by(ReadingCorrectionDB.updated_at.desc()).limit(limit).all())
