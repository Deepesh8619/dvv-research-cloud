"""Small note forms, each read only when every part of it resolves.

    "Ask for all brands selected at S5a, S6, S7."          rows of a grid
    "ASK Q16-Q18 OF ALL EVER PURCHASED - Q2b = YES FOR X"  a block, named in words
    "ASK QS5, QS5a and QS5d in sequence if G = 1"          a block (targets only)

A part that does not resolve (a source that is not there, a label two answers
share, a target not in the document) sets nothing: never a guess.
"""

from __future__ import annotations

import re

from src.core.survey_definition import GRID_TYPES, Expression, Option, Question, SurveyDefinition

REF = r"Q?[A-Z]{1,4}\d+[a-z]?"
_SEP = r"(?:\s*(?:,|&|\band\b))+\s*"  # "QS6b, and QS6c"
QLIST = rf"{REF}(?:\s*[-–—]\s*{REF})?(?:{_SEP}{REF}(?:\s*[-–—]\s*{REF})?)*(?:\s*,)?"
# "ASK Q16-Q18 OF ALL EVER PURCHASED – Q2b = YES FOR X": who, a dash, the rule.
BLOCK_OF_ALL = re.compile(
    rf"^\s*ask\s+(?P<qids>{QLIST})\s+of\s+all\s+[^–—]*?\s[–—-]\s+(?P<rule>[A-Za-z]\w*\s*=\s*.+?)"
    r"\s*\.?\s*$", re.I)
_ROW_SOURCES = re.compile(
    rf"^\s*ask\s+for\s+all\s+[\w\s/-]*?\bselected\s+(?:at|in)\s+"
    rf"(?P<qs>{REF}(?:\s*(?:,|&|\band\b|\bor\b)\s*{REF})*)\s*\.?\s*$", re.I)
_VALUE = re.compile(
    r"^(?P<q>[A-Za-z]\w*)\s*=\s*(?P<val>.+?)(?:\s+for\s+(?P<row>.+?))?\s*\.?$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _index(survey: SurveyDefinition) -> dict[str, int]:
    return {q.qid.upper(): i for i, q in enumerate(survey.questions)}


def _at(name: str, pos: dict[str, int]) -> int | None:
    key = name.upper()
    return pos.get(key, pos.get(re.sub(r"^Q(?=[A-Z])", "", key)))


def block_targets(spec: str, survey: SurveyDefinition) -> list[Question] | None:
    """The questions "QS5, QS5a and Q16-Q18" names ("Q16-Q18" = those in document
    order between them); None when any name is not in the document."""
    pos, out = _index(survey), []
    for part in filter(None, re.split(r"\s*(?:,|&|\band\b)\s*", spec.strip(), flags=re.I)):
        ends = [e for e in re.split(r"\s*[-–—]\s*", part.strip()) if e]
        idx = [_at(e, pos) for e in ends]
        if not ends or len(ends) > 2 or None in idx or idx[0] > idx[-1]:
            return None
        out += survey.questions[idx[0] : idx[-1] + 1]
    return list({q.qid: q for q in out}.values())


def names_earlier_things(expr: Expression, targets: list[Question],
                         survey: SurveyDefinition) -> bool:
    """Who is asked depends only on what came before the first question asked."""
    pos = _index(survey)
    first = min(pos[q.qid.upper()] for q in targets)
    refs = [_at(r, pos) for r in expr.referenced_qids()]
    return bool(refs) and all(i is not None and i < first for i in refs)


def _one(options: list[Option], word: str) -> Option | None:
    """The single option whose label is `word` (or whose code is the number)."""
    hits = [o for o in options if _norm(o.label) == _norm(word)
            or (word.strip().isdigit() and o.code == int(word))]
    return hits[0] if len(hits) == 1 else None


def of_all_condition(text: str, survey: SurveyDefinition) -> Expression | None:
    """"Q2b = YES FOR X": YES against the question's answer labels; "FOR X" is a
    row of a grid, found by label. A grid needs the row; a list must not have one."""
    m = _VALUE.match(text.strip())
    pos = _index(survey)
    if not m or (i := _at(m.group("q"), pos)) is None:
        return None
    src = survey.questions[i]
    if src.type in GRID_TYPES and src.rows and src.cols:
        row = _one(src.rows, m.group("row") or "")
        col = _one(src.cols, m.group("val"))
        if row is None or col is None or not m.group("row"):
            return None
        return Expression(op="in", qid=src.qid, row=row.code, values=[col.code],
                          verbatim_text=text.strip())
    if m.group("row") or not src.options or (opt := _one(src.options, m.group("val"))) is None:
        return None
    return Expression(op="in", qid=src.qid, values=[opt.code], verbatim_text=text.strip())


def row_sources(survey: SurveyDefinition, q: Question, note: str) -> bool:
    """"Ask for all brands selected at S5a, S6, S7" on a grid: each row shown
    where its label was selected at any of those questions. Any row that no
    source offers, or any source not earlier and listed: nothing is set."""
    m = _ROW_SOURCES.match(note)
    if not m or q.type not in GRID_TYPES or not q.rows or any(r.show_if for r in q.rows):
        return False
    pos = _index(survey)
    here = pos.get(q.qid.upper())
    names = re.split(r"\s*(?:,|&|\band\b|\bor\b)\s*", m.group("qs"), flags=re.I)
    found = [_at(n, pos) for n in names]
    if here is None or any(i is None or i >= here for i in found):
        return False
    sources = [survey.questions[i] for i in dict.fromkeys(found)]
    made = []
    for row in q.rows:
        terms = [Expression(op="in", qid=s.qid, values=[o.code])
                 for s in sources for o in s.options or [] if _norm(o.label) == _norm(row.label)]
        if not terms or not _norm(row.label):
            return False
        made.append(terms[0] if len(terms) == 1 else Expression(op="or", children=terms))
    for row, expr in zip(q.rows, made, strict=True):
        expr.verbatim_text = note
        row.show_if = expr
    return True
