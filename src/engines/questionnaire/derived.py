"""Rules for coded hidden variables, proposed from the document and run live.

    D2a. CODE SIZE OF HOUSEHOLD          HID_COMP_SIZE   1 Micro   SS_EMP = 2
         1 / 2 / 3 / 4 or more                           2 Small   SS_EMP= 3-4

A coded hidden variable is a banner every table is cut by, and the document
writes how it is coded in words for a human scripter. Until now the column
was delivered empty: the wording was kept (`Option.definition`) but never run,
because a rule compiled wrong puts numbers nobody authored into that banner.

So a rule is PROPOSED here — from the `Definition` column, from a condition in
the answer's own label, from number bands over the question it is coded from,
or, failing those, by a model writing each code's condition in the document's
own grammar — and a person approves it once. Only then does it land on the
question as `Option.derive_if`, and from then on the value is computed for
every respondent: in the link (so later questions can route on it), on the
server, and in the data file. A variable whose value comes from outside the
answers (a ZIP lookup, the sample source, a random split) is said to be so.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from src.core.survey_definition import (
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.questionnaire.routing_prose import parse_routing

# "DA_USAGE 1, 2, 3, or 4 = 3-5": any of those grid rows answered 3 to 5;
# "DA_USAGE 1, 2, 3, AND 4 = 1-3": every one of them.
_ROWS_RULE = re.compile(
    r"^\s*(?P<qid>[A-Za-z][A-Za-z0-9_]*)\s+(?P<rows>\d+(?:\s*,\s*\d+)*(?:\s*,?\s*(?:or|and)\s+\d+)?)"
    r"\s*=\s*(?P<lo>\d+)(?:\s*-\s*(?P<hi>\d+))?\s*$", re.I)
# "Parent or Caregiver (S11=1 OR 2)", "Recent Bubble Buyer – (Q2c.1=1 OR 2)".
_IN_LABEL = re.compile(r"\(([^()]*[=<>][^()]*)\)")
# "4 or more", "65+", "Under 25", "25-34", "3".
_BAND = re.compile(
    r"^\s*(?:(?P<lt>under|less\s+than|below)\s+(?P<ltn>\d+)"
    r"|(?P<lo>\d+)\s*(?:-|–|to)\s*(?P<hi>\d+)"
    r"|(?P<gte>\d+)\s*(?:\+|or\s+(?:more|over|older|above)|and\s+(?:over|above|older))"
    r"|(?P<eq>\d+))\s*$", re.I)
_SOURCE_NAMED = re.compile(r"\b(?:BASED\s+ON|FROM|AT|USING)\s+(?:RESPONSES?\s+(?:AT|TO)\s+)?"
                           r"(?P<qid>[A-Z][A-Z0-9_]*\d[A-Za-z0-9_.]*)", re.I)
# Values that do not come from any answer: say so rather than guess.
_OUTSIDE = re.compile(r"\bzip\b|postcode|post\s*code|sample\s+source|\bsplit\b|"
                      r"\bpanel\b|\bassign\b|random", re.I)


def rule_text(expr: Expression | None) -> str:
    """A rule as a reviewer reads it: `D2 ≥ 4`, `SS_EMP = 3–4`, `Q2c.1 = 1, 2`."""
    if expr is None:
        return ""
    def name(e: Expression) -> str:
        return f"{e.qid}.{e.row}" if e.row is not None else str(e.qid)
    kids = expr.children or []
    lows = [c.children[0] for c in kids if c.op == "not" and c.children
            and c.children[0].op == "lt"]
    highs = [c.children[0] for c in kids if c.op == "not" and c.children
             and c.children[0].op == "gt"]
    if expr.op == "and" and len(kids) == 2 and len(lows) == 1 and len(highs) == 1:
        return f"{name(lows[0])} = {lows[0].value}–{highs[0].value}"
    if expr.op in ("and", "or"):
        return f" {expr.op.upper()} ".join(
            f"({rule_text(c)})" if c.op in ("and", "or") else rule_text(c) for c in kids)
    if expr.op == "not" and kids and kids[0].op in ("lt", "gt"):
        inner = kids[0]
        return f"{name(inner)} {'≥' if inner.op == 'lt' else '≤'} {inner.value}"
    if expr.op == "not":
        return f"NOT ({rule_text(kids[0])})" if kids else ""
    if expr.op == "in":
        return f"{name(expr)} = {', '.join(str(v) for v in expr.values or [])}"
    symbols = {"eq": "=", "ne": "≠", "lt": "<", "gt": ">", "selected": "includes"}
    symbol = symbols.get(expr.op, expr.op)
    return f"{name(expr)} {symbol} {expr.value}"


def is_coded_hidden(q: Question) -> bool:
    """A computed variable that takes one of several coded values."""
    derived = q.qid.upper().startswith("HID_") or "DERIVED_VARIABLE" in (q.flags or [])
    return derived and bool(q.options) and not (q.qid.upper().startswith("HID_") and q.show_if)


def has_rule(q: Question) -> bool:
    return any(o.derive_if is not None for o in q.options or [])


def needs_rule(q: Question) -> bool:
    return is_coded_hidden(q) and not has_rule(q)


def _between(qid: str, lo: int, hi: int | None, row: str | None = None) -> Expression:
    low = Expression(op="not", children=[Expression(op="lt", qid=qid, value=lo, row=row)])
    if hi is None or hi == lo:
        return Expression(op="eq", qid=qid, value=lo, row=row) if hi == lo else low
    high = Expression(op="not", children=[Expression(op="gt", qid=qid, value=hi, row=row)])
    return Expression(op="and", children=[low, high])


def _rows_rule(text: str) -> Expression | None:
    m = _ROWS_RULE.match(text or "")
    if not m:
        return None
    rows = re.findall(r"\d+", m["rows"])
    hi = int(m["hi"]) if m["hi"] else int(m["lo"])
    parts = [_between(m["qid"], int(m["lo"]), hi, row=r) for r in rows]
    every = bool(re.search(r"\band\b", m["rows"], re.I))
    return parts[0] if len(parts) == 1 else Expression(op="and" if every else "or", children=parts)


def _parse(text: str) -> Expression | None:
    """A condition in the document's grammar, or None if it is not exact."""
    text = (text or "").strip()
    if not text:
        return None
    return _rows_rule(text) or parse_routing(f"IF {text}")


def _source_of(q: Question, survey: SurveyDefinition) -> Question | None:
    by_upper = {x.qid.upper(): x for x in survey.questions}
    named = _SOURCE_NAMED.search(q.text or "")
    if named and named["qid"].upper() in by_upper:
        return by_upper[named["qid"].upper()]
    # D2a is coded from D2: the name without its trailing letter.
    stem = re.sub(r"[a-z]$", "", q.qid)
    if stem != q.qid and stem.upper() in by_upper:
        return by_upper[stem.upper()]
    return None


def _band(label: str, qid: str) -> Expression | None:
    m = _BAND.match(label or "")
    if not m:
        return None
    if m["lt"]:
        return Expression(op="lt", qid=qid, value=int(m["ltn"]))
    if m["lo"]:
        return _between(qid, int(m["lo"]), int(m["hi"]))
    if m["gte"]:
        return _between(qid, int(m["gte"]), None)
    return Expression(op="eq", qid=qid, value=int(m["eq"]))


def rule_from_document(q: Question, survey: SurveyDefinition) -> tuple[Question, str] | None:
    """The question with a rule on every code, and how it was read — or None."""
    after = copy.deepcopy(q)
    how = ""
    rules = [_parse(o.definition or "") for o in after.options]
    if all(rules):
        how = "the document's Definition column"
    else:
        rules = [(_parse(m.group(1)) if (m := _IN_LABEL.search(o.label or "")) else None)
                 for o in after.options]
        if all(rules):
            how = "the conditions written in the answer labels"
    if not all(rules):
        source = _source_of(q, survey)
        # A typed answer counts as a number: "What is your age? ____" is often
        # read as open text, and the evaluator reads numbers stored as text.
        if source is not None and source.type in (QuestionType.NUMERIC, QuestionType.OPEN_TEXT):
            rules = [_band(o.label, source.qid) for o in after.options]
            how = f"the number bands in the labels, over {source.qid}"
        elif source is not None and source.options:
            by_label = {(o.label or "").strip().lower(): o.code for o in source.options}
            codes = [by_label.get((o.label or "").strip().lower()) for o in after.options]
            rules = [Expression(op="in", qid=source.qid, values=[c]) if c is not None else None
                     for c in codes]
            how = f"matching answers at {source.qid}"
    if not all(rules):
        return None
    for option, rule in zip(after.options, rules, strict=True):
        option.derive_if = rule
    _mark_multi_coded(after)
    return after, how


def _tested(expr: Expression) -> frozenset[tuple[str, str | None]]:
    """The (question, row) pairs a rule looks at."""
    if expr.children:
        return frozenset().union(*(_tested(c) for c in expr.children))
    return frozenset({(expr.qid or "", None if expr.row is None else str(expr.row))})


def _mark_multi_coded(q: Question) -> None:
    """Codes that test DIFFERENT things can all hold at once.

    HID_AWARE codes "Google Ads aware" and "Meta Ads aware" from different grid
    rows: one respondent is often both. Codes that split ONE answer into bands
    (D2a over D2) cannot overlap. Recorded so the value is a list, and the data
    file one 0/1 column per code, where several can hold.
    """
    seen = {_tested(o.derive_if) for o in q.options if o.derive_if is not None}
    q.validation = {**(q.validation or {}), "multi_coded": len(seen) > 1}


def comes_from_outside(q: Question) -> bool:
    text = " ".join([q.text or "", *((o.definition or "") for o in q.options or [])])
    return bool(_OUTSIDE.search(text))


def rule_from_model(q: Question, survey: SurveyDefinition) -> tuple[Question, str] | None:
    """Ask a model to write each code's condition in the document's grammar."""
    from src.ai.router import BackendUnavailable, ModelOutputError, run_task

    order = survey.question_order()
    here = order.index(q.qid) if q.qid in order else len(order)
    before = [x for x in survey.questions if x.qid in order[:here]][-25:]
    context = "\n".join(
        f"{x.qid} ({x.type.value}): {(x.text or '')[:120]}"
        + (f" | answers: {', '.join(f'{o.code}={o.label[:30]}' for o in x.options[:12])}"
           if x.options else "")
        for x in before
    )
    prompt = (
        "A market-research questionnaire defines a hidden variable the scripter computes "
        "from earlier answers. For each of its codes, write the condition under which a "
        "respondent gets that code, using ONLY question names below, in this grammar: "
        "`S3 = 2`, `S3 = 1,2`, `S3 = 25-34`, `D2 >= 4`, `Q2c.1 = 1 OR 2`, joined with AND/OR. "
        "Leave a condition empty if the document does not say how to compute it.\n\n"
        f"HIDDEN VARIABLE {q.qid}: {q.text}\n"
        + "\n".join(f"code {o.code}: {o.label}" for o in q.options)
        + f"\n\nSOURCE TEXT:\n{(q.source_text or '')[:1200]}\n\nEARLIER QUESTIONS:\n{context}"
    )
    schema = {
        "type": "object",
        "properties": {"codes": {"type": "array", "items": {
            "type": "object",
            "properties": {"code": {"type": "integer"}, "condition": {"type": "string"}},
            "required": ["code", "condition"]}}},
        "required": ["codes"],
    }
    try:
        response = run_task("questionnaire.resolve", prompt, schema)
    except (BackendUnavailable, ModelOutputError):
        return None
    raw = response.content if isinstance(response.content, dict) else {}
    written = {int(c.get("code")): str(c.get("condition") or "")
               for c in raw.get("codes") or [] if str(c.get("code", "")).lstrip("-").isdigit()}
    known = {x.qid for x in survey.questions}
    after = copy.deepcopy(q)
    for option in after.options:
        rule = _parse(written.get(option.code, ""))
        # Every code needs an exact condition over questions that exist.
        if rule is None or not rule.referenced_qids() <= known:
            return None
        option.derive_if = rule
    _mark_multi_coded(after)
    return after, f"a model ({response.backend}/{response.model}) reading the questions before it"


def derived_values(survey: SurveyDefinition, answers: dict[str, Any]) -> dict[str, Any]:
    """Every approved coded hidden variable's value for this respondent.

    In document order, so one hidden variable may be coded from another
    (HID_GEO_REGION from HID_GEO_DIVISION). The first code whose rule holds
    wins; none holding leaves the variable unset rather than guessed.
    """
    known = dict(answers)
    out: dict[str, Any] = {}
    for q in survey.questions:
        if not is_coded_hidden(q) or not has_rule(q):
            continue
        # Judged only once every question a rule tests is answered: "4 or more"
        # is `not (D2 < 4)`, which holds for someone who has not reached D2.
        held = [o.code for o in q.options
                if o.derive_if is not None
                and o.derive_if.referenced_qids() <= known.keys()
                and evaluate(o.derive_if, known)]
        if not held:
            continue
        value = held if is_multi_coded(q) else held[0]
        out[q.qid] = value
        known[q.qid] = value
    return out


def is_multi_coded(q: Question) -> bool:
    return bool((q.validation or {}).get("multi_coded"))


def with_derived(survey: SurveyDefinition, answers: dict[str, Any]) -> dict[str, Any]:
    """The answers plus computed hidden variables; given answers are never overwritten."""
    return {**derived_values(survey, answers), **answers}
