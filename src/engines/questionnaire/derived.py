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
    if hi == lo:
        return Expression(op="eq", qid=qid, value=lo, row=row)
    low = Expression(op="not", children=[Expression(op="lt", qid=qid, value=lo, row=row)])
    if hi is None:
        return low
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


# A top-level AND/OR between two conditions: only where a NEW condition starts
# (NOT, or a question name followed by its test), so "Q2c.1 = 1 OR 2" and
# "DA_USAGE 1, 2, 3, or 4 = 5" stay whole.
_NEW_CONDITION = r"(?=NOT\b|[A-Za-z][A-Za-z0-9_]*(?:\.\d+)?\s*(?:[=<>!]|\d))"
_JOIN = re.compile(rf"\s+(AND|OR)\s+{_NEW_CONDITION}", re.I)
_NOT = re.compile(r"^\s*NOT\s+", re.I)
# "SS_CHANNELS = 2 and/or 4 ONLY (no others)"
_ONLY = re.compile(r"^\s*(?P<qid>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<codes>[\d,\s/andor]+?)\s+ONLY\b",
                   re.I)
# "Any options outside 2 and 4 selected at SS_CHANNELS"
_OUTSIDE_CODES = re.compile(
    r"^\s*any\s+(?:options?|answers?|codes?)\s+outside\s+(?P<codes>[\d,\s/andor]+?)\s+"
    r"(?:selected\s+)?(?:at|in)\s+(?P<qid>[A-Za-z][A-Za-z0-9_]*)", re.I)


def _question(qid: str, survey: SurveyDefinition | None) -> Question | None:
    return next((x for x in (survey.questions if survey else []) if x.qid.upper() == qid.upper()),
                None)


def _only_or_outside(text: str, survey: SurveyDefinition | None) -> Expression | None:
    """"= 2 and/or 4 ONLY" and "any options outside 2 and 4": over the question's own codes."""
    for pattern, only in ((_ONLY, True), (_OUTSIDE_CODES, False)):
        m = pattern.match(text)
        if not m:
            continue
        named = [int(c) for c in re.findall(r"\d+", m["codes"])]
        source = _question(m["qid"], survey)
        others = [o.code for o in (source.options if source else []) if o.code not in named]
        if not others:
            return None
        qid = source.qid
        if only:
            return Expression(op="and", children=[
                Expression(op="in", qid=qid, values=named),
                Expression(op="not", children=[Expression(op="in", qid=qid, values=others)]),
            ])
        return Expression(op="in", qid=qid, values=others)
    return None


def _parse(text: str, survey: SurveyDefinition | None = None) -> Expression | None:
    """A condition in the document's grammar, or None if it is not exact."""
    text = (text or "").strip().strip("()").strip()
    if not text:
        return None
    whole = _only_or_outside(text, survey) or _rows_rule(text) or parse_routing(f"IF {text}")
    if whole is not None and not _JOIN.search(text):
        return whole
    if _NOT.match(text) and not _JOIN.search(text):
        inner = _parse(_NOT.sub("", text), survey)
        return Expression(op="not", children=[inner]) if inner else None
    # "NOT A AND B": OR binds loosest, then AND.
    for joiner in ("OR", "AND"):
        parts = [p for p in re.split(rf"\s+{joiner}\s+{_NEW_CONDITION}", text, flags=re.I) if p]
        if len(parts) > 1:
            kids = [_parse(p, survey) for p in parts]
            if not all(kids):
                return whole
            return Expression(op=joiner.lower(), children=kids)
    return whole


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
    rules = [_parse(o.definition or "", survey) for o in after.options]
    if all(rules):
        how = "the document's Definition column"
    else:
        rules = [(_parse(m.group(1), survey) if (m := _IN_LABEL.search(o.label or "")) else None)
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


def _leaves(expr: Expression):
    if expr.children:
        for child in expr.children:
            yield from _leaves(child)
    else:
        yield expr


def _tested(expr: Expression) -> frozenset[tuple[str, str | None]]:
    """The (question, row) pairs a rule looks at."""
    return frozenset((e.qid or "", None if e.row is None else str(e.row)) for e in _leaves(expr))


def _mark_multi_coded(q: Question) -> None:
    """Codes that each test a DIFFERENT thing can all hold at once.

    HID_AWARE codes "Google Ads aware" and "Meta Ads aware" from different grid
    rows: one respondent is often both. Codes that split ONE answer into bands
    (D2a over D2) cannot overlap. Recorded so the value is a list, and the data
    file one 0/1 column per code, where several can hold.
    """
    # Only when EVERY code tests something of its own. Bubble C10's audience
    # codes 1-4 split Q2c and code 5 tests Q2a: that is a priority list (first
    # match wins), not memberships — a recent buyer is not also "aware, never
    # purchased".
    tested = [_tested(o.derive_if) for o in q.options if o.derive_if is not None]
    q.validation = {**(q.validation or {}),
                    "multi_coded": len(tested) > 1 and len(set(tested)) == len(tested)}


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
        "`S3 = 2`, `S3 = 1,2`, `S3 = 25-34`, `D2 >= 4`, `Q2c.1 = 1 OR 2`, a grid's rows as "
        "`Q5 1, 2, or 3 = 4-5`, `X = 2 and/or 4 ONLY` for those codes and no others, "
        "joined with AND/OR and NOT. Use only codes and numbers the document states. "
        "Leave exactly one condition empty if that code means everyone else; leave all "
        "empty if the document does not say how to compute it.\n\n"
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
    rules = {o.code: _parse(written.get(o.code, ""), survey) for o in after.options}
    blank = [c for c, r in rules.items() if r is None and not written.get(c, "").strip()]
    if len(blank) == 1 and len(rules) > 1 and all(r for c, r in rules.items() if c != blank[0]):
        # "Unaware (Not selected at Q2a)": the one code left is everyone else.
        rules[blank[0]] = Expression(op="not", children=[
            Expression(op="or", children=[r for c, r in rules.items() if c != blank[0]])])
    for option in after.options:
        rule = rules[option.code]
        # Every code needs an exact condition over questions that exist, using
        # values the document states: a model asked for state regions wrote
        # ZIP codes it made up, over a question that holds a typed ZIP.
        if rule is None or not rule.referenced_qids() <= known or not _grounded(rule, q, survey):
            return None
        option.derive_if = rule
    _mark_multi_coded(after)
    return after, f"a model ({response.backend}/{response.model}) reading the questions before it"


def _grounded(rule: Expression, q: Question, survey: SurveyDefinition) -> bool:
    """Every value a rule tests is a code of that question, or written in the document."""
    by_upper = {x.qid.upper(): x for x in survey.questions}
    written = set(re.findall(r"\d+", " ".join(
        [q.text or "", q.source_text or "",
         *((o.label or "") + " " + (o.definition or "") for o in q.options)])))
    for qid, value in ((e.qid or "", v) for e in _leaves(rule)
                       for v in [*(e.values or []), *([e.value] if e.value is not None else [])]):
        source = by_upper.get(qid.upper())
        codes = {o.code for o in [*(source.options or []), *(source.rows or []),
                                  *(source.cols or [])]} if source else set()
        if value not in codes and str(value) not in written:
            return False
    return True


def derived_values(survey: SurveyDefinition, answers: dict[str, Any]) -> dict[str, Any]:
    """Every approved coded hidden variable's value for this respondent.

    In document order, so one hidden variable may be coded from another
    (HID_GEO_REGION from HID_GEO_DIVISION). A single-coded variable takes the
    first code whose rule holds, a multi-coded one every code that holds; none
    holding leaves the variable unset rather than guessed.
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
