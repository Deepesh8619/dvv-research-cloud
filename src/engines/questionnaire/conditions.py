"""Conditions written in plain words, compiled against the questionnaire.

The copilot (and a person) says "QAGE < 18", "S2 = 1, 2", "QAGE between 18 and
34", "S5 is Fiber or S6 = 3" — never the nested Expression dict the engine
runs. Asking a model to hand-write that dict is where conditions go wrong
silently, so it writes words and this compiles them, checking every question id
exists and every answer it names is one that question offers.

    condition := term (("and" | "or") term)*      ("and" binds tighter)
    term      := QID op value
    op        := = | is | != | is not | < | <= | > | >= | less than | more than
                 | at least | at most | between A and B

A value is a code, a comma/"or" list of codes, or an answer's label. Anything
that does not compile raises ValueError with what to write instead.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, SurveyDefinition

_OPS = [
    (r"between\s+(-?\d+)\s+and\s+(-?\d+)", "between"),
    (r"(?:<=|=<|at most|no more than)", "le"),
    (r"(?:>=|=>|at least|no less than)", "ge"),
    (r"(?:!=|<>|is not|isn't|not)", "ne"),
    (r"(?:<|less than|under|below|younger than)", "lt"),
    (r"(?:>|more than|greater than|over|above|older than)", "gt"),
    (r"(?:==|=|is|equals?)", "eq"),
]
_TERM = re.compile(
    r"^\s*(?P<qid>[A-Za-z][A-Za-z0-9_]*)\s*(?P<op>" + "|".join(p for p, _ in _OPS)
    + r")\s*(?P<value>.*?)\s*$", re.I)


def compile_condition(text: str, survey: SurveyDefinition) -> Expression:
    """The Expression a plain-words condition states, checked against `survey`."""
    source = (text or "").strip().rstrip(".")
    source = re.sub(r"^(?:ask|show|terminate|screen out|end)?\s*(?:only\s+)?if\s+", "",
                    source, flags=re.I)
    if not source:
        raise ValueError("the condition is empty")
    ors = [_ands(part, survey) for part in re.split(r"\s+or\s+(?=[A-Za-z][A-Za-z0-9_]*\s*"
                                                    r"(?:[<>=!]|is\b|not\b|between\b|less\b|"
                                                    r"more\b|at\b|under\b|over\b))",
                                                    source, flags=re.I)]
    expression = ors[0] if len(ors) == 1 else Expression(op="or", children=ors)
    expression.verbatim_text = text.strip()
    return expression


def _ands(text: str, survey) -> Expression:
    terms = [_term(t, survey) for t in re.split(r"\s+and\s+(?![-\d]+\s*$)", text, flags=re.I)
             if t.strip()]
    return terms[0] if len(terms) == 1 else Expression(op="and", children=terms)


def _term(text: str, survey: SurveyDefinition) -> Expression:
    # "between 18 and 34" contains an "and": keep it whole before splitting.
    m = _TERM.match(text)
    if not m:
        raise ValueError(f"cannot read {text.strip()!r}: write it as QID op value, "
                         "e.g. \"QAGE < 18\" or \"S2 = 1, 2\"")
    qid = _known_qid(m.group("qid"), survey)
    op_text, value = m.group("op"), m.group("value").strip()
    op = next(name for pattern, name in _OPS if re.fullmatch(pattern, op_text, re.I))
    if op == "between":
        low, high = (int(x) for x in re.findall(r"-?\d+", op_text))
        return Expression(op="and", children=[Expression(op="gt", qid=qid, value=low - 1),
                                              Expression(op="lt", qid=qid, value=high + 1)])
    if op in ("lt", "gt", "le", "ge"):
        if not re.fullmatch(r"-?\d+", value):
            raise ValueError(f"{qid} {op_text} needs a number, not {value!r}")
        n = int(value)
        return {"lt": Expression(op="lt", qid=qid, value=n),
                "gt": Expression(op="gt", qid=qid, value=n),
                "le": Expression(op="lt", qid=qid, value=n + 1),
                "ge": Expression(op="gt", qid=qid, value=n - 1)}[op]
    codes = _codes(value, survey[qid], qid)
    if op == "ne":
        inner = Expression(op="in", qid=qid, values=codes)
        return Expression(op="not", children=[inner])
    return Expression(op="in", qid=qid, values=codes)


def _known_qid(qid: str, survey: SurveyDefinition) -> str:
    by_upper = {q.qid.upper(): q.qid for q in survey.questions}
    if qid.upper() in by_upper:
        return by_upper[qid.upper()]
    near = [q for q in by_upper.values() if q.upper().startswith(qid.upper()[:2])][:6]
    raise ValueError(f"no question {qid}" + (f"; did you mean {', '.join(near)}?" if near else ""))


def _codes(value: str, question, qid: str) -> list[int]:
    """Codes from "1, 2", "1 or 2", or answer labels ("Fiber Internet")."""
    parts = [p.strip().strip("\"'") for p in re.split(r",|\bor\b|/", value) if p.strip()]
    if not parts:
        raise ValueError(f"{qid}: say which answer, e.g. \"{qid} = 1\"")
    options = question.options or []
    codes = []
    for part in parts:
        if re.fullmatch(r"-?\d+", part):
            code = int(part)
            if options and code not in {o.code for o in options}:
                raise ValueError(f"{qid} has no answer coded {code}; its codes are "
                                 + ", ".join(f"{o.code}={o.label}" for o in options[:12]))
            codes.append(code)
            continue
        hits = [o.code for o in options if o.label.strip().lower() == part.lower()] or \
               [o.code for o in options if part.lower() in o.label.lower()]
        if len(hits) != 1:
            raise ValueError(f"{qid}: {part!r} matches {len(hits)} answers; use the code")
        codes.append(hits[0])
    return codes


def describe_condition(expression: Expression | None) -> str:
    """An Expression back in the plain words a person reviews on a card."""
    if expression is None:
        return "(none)"
    op, qid = expression.op, expression.qid
    if op in ("and", "or"):
        return f" {op} ".join(
            f"({describe_condition(c)})" if c.op in ("and", "or") else describe_condition(c)
            for c in expression.children or [])
    if op == "not":
        inner = (expression.children or [None])[0]
        if inner is not None and inner.op == "in":
            return f"{inner.qid} is not {', '.join(str(v) for v in inner.values or [])}"
        return f"not ({describe_condition(inner)})"
    if op == "in":
        return f"{qid} = {', '.join(str(v) for v in expression.values or [])}"
    symbol = {"eq": "=", "ne": "≠", "gt": ">", "lt": "<", "selected": "has selected"}.get(op, op)
    return f"{qid} {symbol} {expression.value}"
