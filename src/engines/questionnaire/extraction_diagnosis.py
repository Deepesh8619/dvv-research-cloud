"""Why does this study have fewer questions than the document has?

A reviewer opens a study and sees one question where the document has eighty.
The useful answer is never "the AI failed" — it is a structural fact about the
document that a parser did or did not know, and the way to find it is to run
the CURRENT parser over the SAME document and compare what it finds now with
what the study actually holds.

That comparison is the whole point. An extraction is a snapshot taken by the
parser of the day it ran. Every parser fix since then reaches this study only on
re-extraction, so a study can be broken in a way that is already fixed — and
nothing in the stored definition can tell you that. Re-segmenting the archived
source can.

Nothing here changes anything. It reports; a person decides whether to
re-extract, because re-extraction supersedes a definition somebody may have
spent hours reviewing.
"""

from __future__ import annotations

from src.core.survey_definition import Question, QuestionType
from src.intake.questionnaire_loader import (
    _BASE_TAG_RE,
    _QID_RE,
    QuestionChunk,
    _table_qid,
)

# A stored question holding this share of the document's text is not a question,
# it is the document. Set high deliberately: a short questionnaire's single
# long grid can legitimately be a third of the file, and calling that broken
# would cry wolf on instruments that are fine.
_ENGULFED_SHARE = 0.6


def naming_convention(blocks) -> str:
    """How this document marks where one question ends and the next begins."""
    if any(_table_qid(b) is not None for b in blocks):
        return "spec tables (a `Q Name:` row per question)"
    if any(_QID_RE.match(b.text) for b in blocks):
        return "numbered labels (`Q5.`, `S2)`)"
    if any(_BASE_TAG_RE.match(b.text) for b in blocks):
        return "base tags, with nothing numbered (`[BASE: ALL] ...`)"
    return "nothing recognisable — no numbering, no base tags, no spec tables"


def _answerable(q: Question) -> bool:
    return q.type not in (QuestionType.INFO, QuestionType.OPEN_TEXT, QuestionType.NUMERIC)


def diagnose(
    questions: list[Question],
    chunks: list[QuestionChunk],
    blocks,
    can_reextract: bool,
) -> dict:
    """Compare a study's stored questions with what the parser finds today."""
    document_chars = sum(len(b.text) for b in blocks) or 1
    found_now = sum(1 for c in chunks if c.candidate_qid is not None)

    engulfed = [
        q.qid
        for q in questions
        if len(q.source_text or "") / document_chars >= _ENGULFED_SHARE
    ]
    no_answers = [
        q.qid
        for q in questions
        if _answerable(q) and not (q.options or q.rows)
    ]
    unavailable = [
        q.qid for q in questions if "EXTRACTION_UNAVAILABLE" in (q.flags or ())
    ]

    findings: list[str] = []
    if engulfed:
        findings.append(
            f"{len(engulfed)} stored question(s) hold {int(_ENGULFED_SHARE * 100)}%+ of "
            "the document's text each — the document was never split into questions."
        )
    if found_now > len(questions):
        findings.append(
            f"The parser reads {found_now} questions from this document today; the "
            f"study holds {len(questions)}. Re-extracting would pick up the "
            f"{found_now - len(questions)} it now finds."
        )
    if no_answers:
        findings.append(
            f"{len(no_answers)} question(s) offer a respondent nothing to pick: "
            + ", ".join(no_answers[:8])
            + ("…" if len(no_answers) > 8 else "")
        )
    if unavailable:
        findings.append(
            f"{len(unavailable)} question(s) were left unread because the language "
            "model could not be reached when this study was extracted. That is a "
            "backend outage, not a document problem — re-extracting retries them."
        )

    return {
        "questions_stored": len(questions),
        "questions_the_parser_finds_now": found_now,
        "document_convention": naming_convention(blocks),
        "whole_document_in_one_question": engulfed,
        "questions_with_nothing_to_answer": no_answers,
        "questions_the_model_could_not_read": unavailable,
        "findings": findings,
        "can_reextract": can_reextract,
        "note": (
            "Nothing was changed. Re-extraction reads the archived document again "
            "with today's parser and supersedes this version; per-question "
            "approvals belong to the version they were given on and do not carry "
            "over."
            if can_reextract
            else "The document itself was not archived for this study, so it cannot "
            "be re-extracted here — it has to be uploaded again."
        ),
    }
