"""Which answers are shown, read from the notes: anchored answers, "do not
randomize", masks on an earlier question, and an answer hidden by a condition.

Masks use the shape the compiler already reads (`validation["techniques"]
["masks"]`, see mr_techniques.Mask); nothing here invents a format. Every rule
needs its source to be an earlier question that exists; otherwise nothing is set
and the ledger counts the clause as unread.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, QuestionType, SurveyDefinition

_N = r"\d{1,3}(?:\s*(?:-|–|,|&|\band\b|\bor\b)\s*\d{1,3})*"
_QID = r"([A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*)"
_NO_SHUFFLE = re.compile(r"\b(?:do\s+not|don.t|never)\s+(?:randomi[sz]e|rotate|shuffle)\b"
                         r"|\bnot\s+(?:to\s+)?be\s+(?:randomi[sz]ed|rotated)\b", re.I)
_RANDOMISE = re.compile(r"\brandomi[sz]|\brotate\b|\bshuffle", re.I)
_RANDOM_OF = re.compile(r"\bresponse\s+options?\b|\bpunch(?:es)?\b|\banswers?\b|\blist\b", re.I)
# An ordered scale: frequency, agreement, satisfaction or likelihood words, or numbered ends.
_SCALE_WORD = re.compile(
    r"\b(?:always|often|sometimes|rarely|never|several\s+times|once|twice|agree|disagree|satisf\w*"
    r"|(?:un)?likely|definitely|probably|extremely|very|somewhat)\b", re.I)
_NUMBERED = re.compile(r"^\s*\d{1,2}\s*(?:[-–=:.)]|$)")
_FIX = re.compile(rf"\b(?:fix(?:ed)?|lock(?:ed)?)\s+punch(?:es)?\s+({_N}(?:\s*(?:and|&)\s*(?:punch\s+)?{_N})*)", re.I)  # noqa: E501
_KEEP = re.compile(rf"\bkeep\s+punch(?:es)?\s+({_N})[^.]*?\b(?:at\s+the\s+(?:bottom|top|end)|last|first|in\s+place)\b", re.I)  # noqa: E501
_PUNCH_AT_END = re.compile(rf"\bpunch(?:es)?\s+({_N})[^.]*?\bat\s+the\s+(?:bottom|top)\b", re.I)
_GROUP = re.compile(rf"(?:punch(?:es)?\s+)?({_N})\s*(?:\([^)]*\)\s*)?together", re.I)
_IN_ORDER = re.compile(r"\b(?:in|same|fixed)\s+(?:the\s+)?(?:given\s+)?(?:order|sequence)\b"
                       r"|\bdo\s+not\s+(?:randomi[sz]e|shuffle)\s+within\b", re.I)
_WHAT = r"(?:those|brands?|answers?|options?|items?|codes?)"
_SHOWN = re.compile(rf"\b(?:only\s+show|show\s+only|display\s+only|show|display)\s+(?:{_WHAT}\s+)*"
                    rf"(?:that\s+were\s+)?(?P<neg>not\s+)?(?:selected|picked|chosen)\s+(?:at|in|from)\s+{_QID}", re.I)  # noqa: E501
_HIDDEN = re.compile(rf"\bhide\s+(?:{_WHAT}\s+)+(?:that\s+were\s+)?(?:selected|picked|chosen)"
                     rf"\s+(?:at|in|from)\s+{_QID}", re.I)
_NL = r"\d{1,3}(?:\s*(?:,\s*and|,|&|\band\b|-|–)\s*\d{1,3})*"  # with an Oxford comma
_QIDS = rf"{_QID}(?:\s*(?:,\s*and|,|&|\band\b)\s*{_QID})*"
_EXCEPT = re.compile(rf"\bshow\s+all\s+(?:response\s+)?(?:options|answers|items|brands|codes)\s+"
                     rf"except\s+(?:for\s+)?(?:the\s+)?(?:option|answer|item|brand|code)s?\s+"
                     rf"(?:that\s+)?(?:was|were)?\s*(?:selected|picked|chosen)\s+(?:at|in|from)\s+{_QID}", re.I)  # noqa: E501
_PAIRED = re.compile(rf"\bonly\s+show\s+punch(?:es)?\s+({_NL})\s+if\s+(?:the\s+)?"
                     rf"(?:corresponding|matching|respective)\s+\w+\s+(?:was|were)\s+"
                     rf"(?:selected|picked|chosen)\s+(?:\(?punch\s+(\d{{1,3}})\)?\s+)?"
                     rf"(?:at|in|from)\s+({_QIDS})", re.I)
_CODES_IF = re.compile(rf"^(?!.*\b(?:do\s+not|don.t|never)\b).*?\bshow\s+(?:only\s+)?(?:punch(?:es)?|codes?)\s+({_N})\s+if\s+(.+)$", re.I)  # noqa: E501
_TOGETHER = re.compile(r"\b(?:do\s+not|don.t|never)\s+allow\s+punch(?:es)?\s+(\d{1,3})\s*"
                       r"(?:and|&|,)\s*(\d{1,3})\s+(?:to\s+be\s+)?(?:selected|chosen|picked)"
                       r"\s+together", re.I)
_NOT_IF = re.compile(rf"\b(?:do\s+not|don.t)\s+show\s+punch(?:es)?\s+({_N})\s+if\s+(.+)$", re.I)


def is_scale(q) -> bool:
    """The answers read as an ordered scale (a plain list of names never does)."""
    labels = [o.label or "" for o in q.options or []]
    numbered = [bool(_NUMBERED.match(x)) for x in labels]
    return sum(bool(_SCALE_WORD.search(x)) for x in labels) >= 2 \
        or (len(labels) >= 3 and numbered[0] and numbered[-1])


def _unclear_randomise(q, notes: list[str], made: list[dict]) -> None:
    """"RANDOMIZE ORDER" that names no list, over a scale: probably the screens, not the answers."""
    said = [n for n in notes if _RANDOMISE.search(n) and not _NO_SHUFFLE.search(n)]
    if q.randomize_options and said and not any(_RANDOM_OF.search(n) for n in said) \
            and is_scale(q):
        q.randomize_options = False
        q.validation = {**(q.validation or {}), "randomize_unclear": said[0]}
        made.append({"qid": q.qid, "set": "randomize unclear", "said": said[0]})


def apply(survey: SurveyDefinition, q, notes: list[str], made: list[dict]) -> None:
    from src.engines.questionnaire.note_rules import _codes

    by_code = {o.code: o for o in q.options or []}
    _unclear_randomise(q, notes, made)
    for note in notes:
        # A question-wide "do not randomize"; one about named punches is not.
        if _NO_SHUFFLE.search(note) and not re.search(r"\bpunch", note, re.I) \
                and q.randomize_options:
            q.randomize_options = False
            made.append({"qid": q.qid, "set": "randomize off", "said": note})
        together = re.search(r"\btogether\b", note, re.I)
        if together:
            _together(q, note, by_code, made)  # pins nothing: the group moves as one
        if not (together and _KEEP.search(note)):
            found = [m for rx in (_FIX, _KEEP, _PUNCH_AT_END) for m in rx.finditer(note)]
            for code in [c for m in found for c in _codes(m.group(1))]:
                if (o := by_code.get(code)) is not None and not o.anchored:
                    o.anchored = True
                    made.append({"qid": q.qid, "set": f"anchored {code}", "said": note})
        _never_together(q, note, by_code, made)
        _except(survey, q, note, by_code, made)
        _paired(survey, q, note, by_code, made)
        _mask(survey, q, note, made)
        _codes_if(survey, q, note, by_code, made)
        _hide_if(survey, q, note, by_code, made)


def groups_in(note: str) -> list[list[int]]:
    """Every run of punches the note says to keep together (two or more codes)."""
    from src.engines.questionnaire.note_rules import _codes

    found = [list(dict.fromkeys(_codes(m.group(1)))) for m in _GROUP.finditer(note)]
    return [g for g in found if len(g) > 1]


def _together(q, note: str, by_code: dict, made: list[dict]) -> None:
    """Store `keep_together` only when every group is real, whole and disjoint."""
    groups = groups_in(note)
    kept = (q.validation or {}).get("keep_together") or []
    taken = [c for g in kept for c in g["codes"]]
    flat = [c for g in groups for c in g]
    if not groups or len(set(flat)) != len(flat) or set(flat) & set(taken):
        return
    if any(c not in by_code or by_code[c].anchored for c in flat):
        return
    within = not _IN_ORDER.search(note)
    new = [{"codes": g, "shuffle_within": within} for g in groups]
    q.validation = {**(q.validation or {}), "keep_together": [*kept, *new]}
    made.append({"qid": q.qid, "set": f"keep together {flat}", "said": note})


def _earlier(survey: SurveyDefinition, q, qid: str):
    """The question named, if it exists, comes before `q` and has answers to carry."""
    for other in survey.questions:
        if other is q:
            return None
        if other.qid.upper() == qid.upper():
            return other if (other.options or other.rows) else None
    return None


def _mask(survey: SurveyDefinition, q, note: str, made: list[dict]) -> None:
    if not (q.options or q.rows):
        return
    m = _SHOWN.search(note) or _HIDDEN.search(note)
    if not m:
        return
    hidden = m.re is _HIDDEN
    source = _earlier(survey, q, m.group(m.lastindex))
    if source is None:
        return
    mode = "not_selected" if hidden or m.groupdict().get("neg") else "selected"
    techniques = (q.validation or {}).get("techniques") or {}
    masks = techniques.get("masks") or []
    if any(x.get("source_qid") == source.qid for x in masks):
        return
    mask = {"source_qid": source.qid, "mode": mode, "exclude_qid": None, "verbatim": note}
    q.validation = {**(q.validation or {}), "techniques": {**techniques, "masks": [*masks, mask]}}
    made.append({"qid": q.qid, "set": f"mask {mode} {source.qid}", "said": note})


def _hide_if(survey: SurveyDefinition, q, note: str, by_code: dict, made: list[dict]) -> None:
    from src.engines.questionnaire.note_rules import _codes, _compile

    m = _NOT_IF.search(note)
    if not m:
        return
    expr = _compile(m.group(2), q.qid, survey)
    earlier = {x.qid.upper() for x in survey.questions[: survey.questions.index(q)]}
    if expr is None or not {x.upper() for x in expr.referenced_qids()} <= earlier:
        return
    for code in _codes(m.group(1)):
        if (o := by_code.get(code)) is not None and o.show_if is None:
            o.show_if = Expression(op="not", children=[expr], verbatim_text=note)
            made.append({"qid": q.qid, "set": f"show_if on punch {code}", "said": note})


def _never_together(q, note: str, by_code: dict, made: list[dict]) -> None:
    """"Do not allow punch 1 and 2 together": only a multi, only codes it offers."""
    m = _TOGETHER.search(note)
    if not m or q.type is not QuestionType.MULTI:
        return
    pair = sorted({int(m.group(1)), int(m.group(2))})
    pairs = (q.validation or {}).get("never_together") or []
    if len(pair) == 2 and all(c in by_code for c in pair) and pair not in pairs:
        q.validation = {**(q.validation or {}), "never_together": [*pairs, pair]}
        made.append({"qid": q.qid, "set": f"never together {pair}", "said": note})


def _except(survey: SurveyDefinition, q, note: str, by_code: dict, made: list[dict]) -> None:
    """"Show all options EXCEPT the one selected at S6": the list is this question's own."""
    m = _EXCEPT.search(note)
    source = _earlier(survey, q, m.group(m.lastindex)) if m and q.options else None
    if source is None or not source.options or not {o.code for o in source.options} <= set(by_code):
        return
    techniques = (q.validation or {}).get("techniques") or {}
    # The extractor reads this wording as a plain not_selected mask; this one is exact.
    masks = [x for x in techniques.get("masks") or []
             if not (x.get("exclude_qid") == source.qid
                     or (x.get("verbatim") and x["verbatim"] in note))]
    mask = {"source_qid": q.qid, "mode": "list_except", "exclude_qid": source.qid,
            "verbatim": note}
    q.validation = {**(q.validation or {}), "techniques": {**techniques, "masks": [*masks, mask]}}
    made.append({"qid": q.qid, "set": f"mask list_except {source.qid}", "said": note})


def _paired(survey: SurveyDefinition, q, note: str, by_code: dict, made: list[dict]) -> None:
    """Punch i is shown if the i-th source was answered with the punch the note names.

    Without a named punch the sentence does not say what "selected" means, so
    nothing is set (the ledger counts it unread).
    """
    from src.engines.questionnaire.note_rules import _codes, _compile

    m = _PAIRED.search(note)
    if not m or not m.group(2):
        return
    codes, qids = _codes(m.group(1)), re.findall(_QID, m.group(3))
    if len(codes) != len(qids) or len(set(codes)) != len(codes):
        return
    sources = [_earlier(survey, q, x) for x in qids]
    if any(s is None for s in sources) or any(c not in by_code for c in codes):
        return
    exprs = [_compile(f"{s.qid}={m.group(2)}", q.qid, survey) for s in sources]
    if any(e is None for e in exprs):
        return
    for code, expr in zip(codes, exprs, strict=True):
        if by_code[code].show_if is None:
            by_code[code].show_if = Expression.from_dict(expr.to_dict())
            by_code[code].show_if.verbatim_text = note
            made.append({"qid": q.qid, "set": f"show_if on punch {code}", "said": note})


def _codes_if(survey: SurveyDefinition, q, note: str, by_code: dict, made: list[dict]) -> None:
    """"Show codes 1-5 if S2=1": those answers need the condition. With no condition
    it is a fixed subset, which is not a mask, so nothing is set."""
    from src.engines.questionnaire.note_rules import _codes, _compile

    m = _CODES_IF.search(note)
    if not m:
        return
    expr = _compile(m.group(2), q.qid, survey)
    earlier = {x.qid.upper() for x in survey.questions[: survey.questions.index(q)]}
    if expr is None or not {x.upper() for x in expr.referenced_qids()} <= earlier:
        return
    for code in _codes(m.group(1)):
        if (o := by_code.get(code)) is not None and o.show_if is None:
            o.show_if = Expression.from_dict(expr.to_dict())
            o.show_if.verbatim_text = note
            made.append({"qid": q.qid, "set": f"show_if on punch {code}", "said": note})
