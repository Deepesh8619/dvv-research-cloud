"""Which answers are shown, read from the notes: anchored answers, "do not
randomize", masks on an earlier question, and an answer hidden by a condition.

Masks use the shape the compiler already reads (`validation["techniques"]
["masks"]`, see mr_techniques.Mask); nothing here invents a format. Every rule
needs its source to be an earlier question that exists; otherwise nothing is set
and the ledger counts the clause as unread.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, SurveyDefinition

_N = r"\d{1,3}(?:\s*(?:-|–|,|&|\band\b|\bor\b)\s*\d{1,3})*"
_QID = r"([A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*)"
_NO_SHUFFLE = re.compile(r"\b(?:do\s+not|don.t|never)\s+(?:randomi[sz]e|rotate|shuffle)\b"
                         r"|\bnot\s+(?:to\s+)?be\s+(?:randomi[sz]ed|rotated)\b", re.I)
_FIX = re.compile(rf"\b(?:fix(?:ed)?|lock(?:ed)?)\s+punch(?:es)?\s+({_N}(?:\s*(?:and|&)\s*(?:punch\s+)?{_N})*)", re.I)  # noqa: E501
_KEEP = re.compile(rf"\bkeep\s+punch(?:es)?\s+({_N})[^.]*?\b(?:at\s+the\s+(?:bottom|top|end)|last|first|in\s+place)\b", re.I)  # noqa: E501
_PUNCH_AT_END = re.compile(rf"\bpunch(?:es)?\s+({_N})[^.]*?\bat\s+the\s+(?:bottom|top)\b", re.I)
_WHAT = r"(?:those|brands?|answers?|options?|items?|codes?)"
_SHOWN = re.compile(rf"\b(?:only\s+show|show\s+only|display\s+only|show|display)\s+(?:{_WHAT}\s+)*"
                    rf"(?:that\s+were\s+)?(?P<neg>not\s+)?(?:selected|picked|chosen)\s+(?:at|in|from)\s+{_QID}", re.I)  # noqa: E501
_HIDDEN = re.compile(rf"\bhide\s+(?:{_WHAT}\s+)+(?:that\s+were\s+)?(?:selected|picked|chosen)"
                     rf"\s+(?:at|in|from)\s+{_QID}", re.I)
_NOT_IF = re.compile(rf"\b(?:do\s+not|don.t)\s+show\s+punch(?:es)?\s+({_N})\s+if\s+(.+)$", re.I)


def apply(survey: SurveyDefinition, q, notes: list[str], made: list[dict]) -> None:
    from src.engines.questionnaire.note_rules import _codes

    by_code = {o.code: o for o in q.options or []}
    for note in notes:
        # A question-wide "do not randomize"; one about named punches is not.
        if _NO_SHUFFLE.search(note) and not re.search(r"\bpunch", note, re.I) \
                and q.randomize_options:
            q.randomize_options = False
            made.append({"qid": q.qid, "set": "randomize off", "said": note})
        if _KEEP.search(note) and re.search(r"\btogether\b", note, re.I):
            pass  # "keep punch 9 and 10 together" shuffles as a pair; it pins nothing
        else:
            found = [m for rx in (_FIX, _KEEP, _PUNCH_AT_END) for m in rx.finditer(note)]
            for code in [c for m in found for c in _codes(m.group(1))]:
                if (o := by_code.get(code)) is not None and not o.anchored:
                    o.anchored = True
                    made.append({"qid": q.qid, "set": f"anchored {code}", "said": note})
        _mask(survey, q, note, made)
        _hide_if(survey, q, note, by_code, made)


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
