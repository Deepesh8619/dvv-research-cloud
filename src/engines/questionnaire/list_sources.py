"""A list the document states once and uses again, filled in where it is used.

    Q27. Which of these statements do you find the most believable?
         [SHOW Q26 LIST. RANDOMIZE]
    Q28. ...the least believable?  [SHOW Q26 LIST EXCEPT FOR Q27 SELECTION]
    Q2c. When did you last buy from each of the following brands?
         [PN: FOR Q2c SHOW BRANDS SELECTED AT Q2b]
    Q9.  How likely are you to consider each of these brands?   (a 0-10 strip)

None of them writes its list out: it is Q26's statements, Q2b's brands, the
study's brand list. Each was detected — the mask was even recorded — and then
fielded with NO answers, a question on a blank page.

Two cases, told apart because they are not equally certain:

* The document NAMES the source ("SHOW Q26 LIST"). The list is copied from
  there and the mask narrows it per respondent, as it already would. Certain.
* The wording only POINTS at it ("each of these brands") and no source is
  named. The nearest earlier brand list is copied and the question is flagged:
  that is almost always right and occasionally the wrong list, and which list
  decides the data columns, so a person confirms it.

What is copied never includes the source's "Other (specify)" or "None" — they
belong to that question, not to a list of brands someone is rating.
"""

from __future__ import annotations

import re
from dataclasses import replace

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire.carousel import coded, list_for
from src.engines.questionnaire.prose_directives import is_terminal_option

_CHOICE_TYPES = {QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN, QuestionType.RANK}
# "each of these brands", "the following skincare brands", "these statements"
_POINTS_AT_A_LIST = re.compile(
    r"\b(?:each of )?(?:these|the following|those)\b[^.?]{0,30}"
    r"\b(brands?|statements?|stores?|products?)\b",
    re.I,
)
_MIN_LIST = 3
_WRITE_IN = re.compile(r"\(\s*specify\s*\)|\bnot listed\b|^another\b", re.I)


def points_at_a_list(text: str) -> bool:
    """True when the wording rates several things ("each of these brands")."""
    return bool(_POINTS_AT_A_LIST.search(text or ""))


def _items(source: Question) -> list[Option]:
    """What a question offers as a list: its options, or a grid's rows."""
    items = source.options if source.options else (source.rows or [])
    return [
        replace(o, show_if=None)
        for o in items
        # "Another brand (Specify)" is that question's write-in, not a brand.
        if not is_terminal_option(o.label) and not o.open_other and not _WRITE_IN.search(o.label)
    ]


def _needs_list(q: Question) -> bool:
    if q.type is QuestionType.GRID_SINGLE:
        # Only a grid whose scale IS known. With neither half ("for each
        # category, which brands" — a carousel), borrowing rows put another
        # question's answers down the side of a grid that has no scale.
        return not q.rows and bool(q.cols)
    return q.type in _CHOICE_TYPES and not q.options


def _fill(q: Question, items: list[Option]) -> None:
    if q.type is QuestionType.GRID_SINGLE:
        q.rows = items
    else:
        q.options = items
    if schema.FLAG_EMPTY_OPTIONS in q.flags:
        q.flags.remove(schema.FLAG_EMPTY_OPTIONS)


def fill_lists(survey: SurveyDefinition) -> list[dict]:
    """Fill every list the document states elsewhere. Returns what was filled."""
    by_upper = {q.qid.upper(): q for q in survey.questions}
    filled: list[dict] = []
    seen: list[Question] = []  # questions before this one, in order

    for qid in survey.question_order():
        q = survey[qid] if qid in survey else None
        if q is None:
            continue
        study = list_for(survey, q.qid) if _needs_list(q) else None
        if study and not ((q.validation or {}).get("techniques") or {}).get("masks"):
            # "FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF 10 BRANDS": named for
            # exactly these questions, so nothing to confirm.
            _fill(q, coded(survey, study["items"], q))
            q.extraction_evidence["options"] = study["verbatim"]
            filled.append({"qid": q.qid, "from": study["verbatim"], "named": True})
            seen.append(q)
            continue
        if _needs_list(q):
            source, named = _named_source(q, by_upper), True
            if source is None:
                source, named = _nearest_list(q, seen), False
            if source is not None and len(_items(source)) >= _MIN_LIST:
                _fill(q, _items(source))
                how = f"the list at {source.qid}"
                if not named:
                    how += " — the document does not name it"
                q.extraction_evidence["options"] = how
                if not named:
                    q.flags.append(schema.FLAG_LIST_INHERITED)
                filled.append({"qid": q.qid, "from": source.qid, "named": named})
        seen.append(q)

    if filled:
        survey.metadata["lists_filled"] = filled
    _point_generic_pipes(survey)
    return filled


# "[INSERT RATING]", "[INSERT ANSWER]": a pipe that names no question means the
# one just answered. Left unresolved, it printed as a gap: "Why did you rate
# this message for Bubble a ?"
_GENERIC_PIPE = {"RATING", "SCORE", "ANSWER", "RESPONSE", "SELECTION", "CHOICE"}
_ONE_VALUE = {QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.NUMERIC}


def _point_generic_pipes(survey: SurveyDefinition) -> None:
    previous: Question | None = None
    for qid in survey.question_order():
        q = survey[qid] if qid in survey else None
        if q is None:
            continue
        techniques = (q.validation or {}).get("techniques") or {}
        for pipe in techniques.get("pipes") or []:
            name = str(pipe.get("name") or "").strip().upper()
            # Only when "the answer just given" is ONE value: after a grid,
            # "this message's rating" names a row nobody identified.
            if (
                not pipe.get("source_qid") and name in _GENERIC_PIPE
                and previous is not None and previous.type in _ONE_VALUE
            ):
                pipe["source_qid"] = previous.qid
                q.extraction_evidence["pipe"] = (
                    f"[INSERT {name}] names no question — read as {previous.qid}, the one before"
                )
        if q.type is not QuestionType.INFO:
            previous = q


def _named_source(q: Question, by_upper: dict[str, Question]) -> Question | None:
    """The question a mask names as the list's source ("SHOW Q26 LIST")."""
    techniques = (q.validation or {}).get("techniques") or {}
    for mask in techniques.get("masks") or []:
        # The parser upper-cases what it reads ("Q2B"); the question is "Q2b".
        source = by_upper.get(str(mask.get("source_qid") or "").upper())
        if source is not None and source is not q:
            return source
    return None


def _nearest_list(q: Question, before: list[Question]) -> Question | None:
    """The closest earlier list of the kind the wording points at."""
    pointed = _POINTS_AT_A_LIST.search(q.text or "")
    if not pointed:
        return None
    kind = pointed.group(1).lower().rstrip("s")  # brand / statement / store
    for candidate in reversed(before):
        if len(_items(candidate)) < _MIN_LIST:
            continue
        if kind in (candidate.text or "").lower():
            return candidate
    return None
