"""An answer that ended up inside the question's wording.

Seen in field on a live pharmacy study, at question 18 of 73:

    Looking again at these pharmacies you've heard of before — have you ever
    personally used each one for your pharmacy needs? (such as prescription
    fills or refills, consultations with a pharmacist, or other pharmacy
    services, either in-person or through online/delivery) Yes

    ( ) No

"Yes" is not part of the question. It is the first answer, written on the same
line as the wording it followed in the document — a layout the chunker cannot
see once the formatting is gone — so the respondent was offered a yes/no
question with only "No" to choose, and every one of them had to either answer
No or abandon the interview.

This repairs the narrow, unambiguous case and nothing else: the trailing word
is a yes/no answer, and its opposite is sitting in the answer list. Anything
less certain than that is left alone and flagged for a reviewer, because
inventing an option nobody wrote is a worse failure than showing a short list.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import GRID_TYPES, Option, QuestionType, SurveyDefinition

#: The pairs worth repairing. Each side is what a document actually prints.
_PAIRS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("yes",), ("no",)),
    (("no",), ("yes",)),
]

#: "… online/delivery) Yes" — the swallowed answer sits at the very end, after
#: the question mark or the closing bracket that ended the real wording.
_TRAILING = re.compile(r"[?)\]]\s*(?P<word>[A-Za-z]{2,3})\s*$")

#: The codes a yes/no question is punched with when the document does not say.
_CODES = {"yes": 1, "no": 2}


def repair_swallowed_options(survey: SurveyDefinition) -> list[str]:
    """Give back the answer that was absorbed into the wording. Returns qids."""
    repaired: list[str] = []
    for question in survey.questions:
        if _repair(question):
            repaired.append(question.qid)
    if repaired:
        survey.metadata["repaired_swallowed_options"] = repaired
    return repaired


def _repair(question: Any) -> bool:
    if question.type in GRID_TYPES:
        # Typed a grid, holding one answer and neither rows nor columns: the
        # shape of a yes/no question whose "Yes" was eaten by the wording.
        # Q4a of the live pharmacy study is exactly this, and skipping it
        # because of the type left the question with "No" as its only answer.
        if question.rows or question.cols or len(question.options or []) != 1:
            return False
        question.type = QuestionType.SINGLE
    elif question.type not in (QuestionType.SINGLE, QuestionType.MULTI):
        return False
    labels = [(o.label or "").strip().lower() for o in (question.options or [])]
    # Exactly one answer left standing: a longer list means the document's own
    # list survived, and a trailing word is part of the wording.
    if len(labels) != 1:
        return False

    match = _TRAILING.search(question.text or "")
    if not match:
        return False
    missing = match.group("word").lower()
    for word, opposite in _PAIRS:
        if missing in word and labels[0] in opposite:
            break
    else:
        return False

    question.text = (question.text or "")[: match.start()] + match.group(0)[0]
    kept = question.options[0]
    restored = Option(code=_CODES[missing], label=missing.capitalize())
    # The document's own code for the surviving answer is authoritative; only
    # the restored one is assigned, and it takes the code left over.
    if kept.code == restored.code:
        restored.code = 2 if restored.code == 1 else 1
    question.options = (
        [restored, kept] if _CODES[missing] <= (kept.code or 99) else [kept, restored]
    )
    question.extraction_evidence["options"] = (
        f"'{restored.label}' was written on the end of the question's own line and "
        f"read as part of the wording; restored as an answer"
    )
    return True
