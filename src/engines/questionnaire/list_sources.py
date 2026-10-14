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
# "each of these brands", "the following skincare brands", "these statements",
# "these pharmacies", "the following ways of getting prescriptions".
#
# The noun is NOT a fixed list. It used to be (brands|statements|stores|
# products), which is a skincare-and-advertising view of the world: a
# healthcare study asking about "these pharmacies" matched nothing, so eight
# questions fielded with no answers at all on a live pharmacy study. Whatever
# the document calls the things it is listing, the question after it says
# "these <that word>" — that is the rule, and it holds in every category.
_POINTS_AT_A_LIST = re.compile(
    r"\b(?:each|any|one|all) of these\b|\b(?:these|those|the following)\b",
    re.I,
)
#: Where the noun phrase ends. Everything from here on is the rest of the
#: sentence, not the name of the thing being listed — matching "you" against
#: earlier wording would find a "list" in almost any question.
_PHRASE_ENDS = frozenset({
    "you", "your", "yours", "have", "has", "had", "do", "does", "did", "would",
    "will", "are", "is", "was", "were", "can", "could", "should", "may",
    "which", "who", "that", "what", "when", "where", "how", "why",
    "of", "in", "for", "to", "and", "or", "with", "from", "by", "about",
    "the", "a", "an", "if", "any", "best", "most", "least", "please",
    # Words that follow "these" without naming a list of answers.
    "question", "questions", "screen", "screens", "page", "pages", "people",
    "respondent", "respondents", "same", "other", "others", "option",
    "options", "answer", "answers", "ones", "one",
})
_MIN_LIST = 3
#: Words that make an answer a point on a SCALE rather than a thing in a list.
#: A scale question mentions the same noun ("How familiar are you with each of
#: these pharmacies?") and sits nearer, so without this the borrowed "list" is
#: Yes/No/Not sure — which is worse than an empty question, because it looks
#: answerable and writes the wrong columns into the data file.
_SCALE_WORD = re.compile(
    r"^\s*(yes|no|not sure|don'?t know|maybe|n/?a|none|never|always|sometimes|often|rarely)\b"
    r"|\b(very|somewhat|extremely|slightly|not at all|neither|strongly|"
    r"likely|unlikely|satisfied|dissatisfied|familiar|agree|disagree|"
    r"more than|less than|within the past|i'?ve heard|i know)\b",
    re.I,
)
_WRITE_IN = re.compile(r"\(\s*specify\s*\)|\bnot listed\b|^another\b", re.I)


def pointed_nouns(text: str) -> list[str]:
    """The things a wording points back at: "these pharmacies" -> [pharmacies].

    Reads the words just after "these"/"the following"/"those", nearest first,
    so "these skincare brands" answers "brands" before "skincare" and either
    can find the earlier question that listed them.
    """
    found: list[str] = []
    for match in _POINTS_AT_A_LIST.finditer(text or ""):
        window = (text or "")[match.end() : match.end() + 40]
        phrase: list[str] = []
        # Two letters is enough: "of", "in", "to" END a noun phrase, and a
        # tokenizer that cannot see them reads "ways of getting prescriptions"
        # as one long name.
        for word in re.findall(r"[A-Za-z][A-Za-z-]+", window):
            if word.lower() in _PHRASE_ENDS:
                break  # the noun phrase is over: "these pharmacies HAVE you…"
            phrase.append(word.lower())
        # "these skincare brands" names brands, of which skincare is the kind.
        for word in reversed(phrase[:3]):
            if word not in found:
                found.append(word)
    return found


def points_at_a_list(text: str) -> bool:
    """True when the wording rates several things ("each of these brands")."""
    return bool(pointed_nouns(text))


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


def canonical_sources(survey: SurveyDefinition) -> None:
    """Store the real qid in every mask/pipe source.

    The technique parser upper-cases what it reads ("Q2B"); the question is
    "Q2b". Matching ignores case, but the stored name must be the question's
    own, or every exact-match consumer (preflight, dryrun, links, QC) finds
    nothing and the list silently draws from no question.
    """
    by_upper = {q.qid.upper(): q.qid for q in survey.questions}
    for q in survey.questions:
        t = (q.validation or {}).get("techniques") or {}
        for item, keys in [(m, ("source_qid", "exclude_qid")) for m in t.get("masks") or []] + [
            (p, ("source_qid",)) for p in t.get("pipes") or []
        ]:
            for key in keys:
                real = by_upper.get(str(item.get(key) or "").upper())
                if real:
                    item[key] = real


def fill_lists(survey: SurveyDefinition) -> list[dict]:
    """Fill every list the document states elsewhere. Returns what was filled."""
    canonical_sources(survey)
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


def _stems(noun: str) -> set[str]:
    """The forms of a noun a document might use: pharmacies / pharmacy."""
    forms = {noun}
    if noun.endswith("ies"):
        forms.add(noun[:-3] + "y")
    elif noun.endswith("es"):
        forms.add(noun[:-2])
    if noun.endswith("s"):
        forms.add(noun[:-1])
    return {f for f in forms if len(f) >= 3}


def _looks_like_a_scale(question: Question) -> bool:
    """True when this question's answers are degrees, not things."""
    items = _items(question)
    if len(items) < 2:
        return False
    scale_like = sum(1 for o in items if _SCALE_WORD.search(o.label or ""))
    return scale_like >= max(2, len(items) // 2)


def _nearest_list(q: Question, before: list[Question]) -> Question | None:
    """The closest earlier list of the kind the wording points at."""
    nouns = pointed_nouns(q.text or "")
    if not nouns:
        return None
    for noun in nouns:  # nearest word first: "these skincare BRANDS"
        forms = _stems(noun)
        for candidate in reversed(before):
            if len(_items(candidate)) < _MIN_LIST or _looks_like_a_scale(candidate):
                continue
            text = (candidate.text or "").lower()
            if any(form in text for form in forms):
                return candidate
    return None
