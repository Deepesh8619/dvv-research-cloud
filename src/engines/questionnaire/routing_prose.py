"""Turn a scripter's routing directive into a real `Expression`.

Prose questionnaires state the base for every question in the margin:

    [IF S2 = 2 or S2=3]
    [ONLY ASK H0 - H6 IF S2 = 1]
    [IF (D1 = 3, 4, 5, OR 6)]
    [IF D6 < 2013 AND S2=1]

Until now these were kept only as directive strings, so `show_if` stayed empty
on every prose document and the two checks that depend on it — routing
cross-validation and the condition audit — had nothing to check. On the Federal
Reserve SHED 2014 instrument that was 0 of 104 questions validated while the
base for almost all of them was written plainly in the document.

The parser is deliberately literal. Anything it cannot read exactly — mixed
AND/OR without parentheses, a reference to something that is not a question id,
an operator it does not know — returns None so the directive stays verbatim for
a human. Inventing a base is worse than admitting there isn't one: a wrong
`show_if` silently reclassifies real respondents as routing violations.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression

# "IF", "ONLY ASK <anything> IF", "ASK IF", "ASK ONLY IF" — everything up to and
# including the last IF is preamble; the condition is what follows.
# Also "SHOW IF", "DISPLAY IF", "PN: SHOW IF" — the programmer-note spelling is
# how a screen or an intro block states its base, and it routes exactly like a
# question's own "ASK IF".
_PREAMBLE_RE = re.compile(
    r"^\s*(?:PN\s*:\s*)?(?:ONLY\s+)?"
    r"(?:ONLY\s+ASK\b.*?|ASK\s+(?:ONLY\s+)?|SHOW\s+|DISPLAY\s+)?\bIF\b\s*",
    re.I,
)

# One comparison: a question id, an operator, then one or more numbers.
# The id is the same shape the segmenter recognises as a label (D2, S2, ED11A,
# PPT612) so a condition can only ever reference something that could be a
# question.
# Also the spec-table names ("GA_GOOGLE_TOOL") and one row of a grid ("Q38.11"
# is row 11 of Q38): both were unreadable, so "ASK IF Q38.11=6-10" and every
# condition naming an underscore question went unapplied.
_ATOM_RE = re.compile(
    r"^\s*\(?\s*([A-Za-z]{1,4}\d+[A-Za-z]?(?:\.\d+)?|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9])"
    r"\s*(=|==|<>|!=|>=|<=|>|<)\s*(.*?)\s*\)?\s*$"
)
_VALUES_RE = re.compile(r"\d+")
# "S1 = 26-60", "AGE = 26 to 60" — a band, not a punch list. Written constantly
# in recruitment paths, where the ranges are the sample definition itself.
_RANGE_RE = re.compile(r"^(\d+)\s*(?:-|–|—|\bto\b|\bthrough\b)\s*(\d+)$", re.I)
# "C1=18+", "AGE = 18 OR OLDER" — a floor with no ceiling.
_OPEN_RANGE_RE = re.compile(r"^(\d+)\s*(?:\+|\bor older\b|\band older\b|\bplus\b)$", re.I)
# "1 (ANUA) OR 11 (MEDICUBE) OR 98" — the punch list is the data; the labels in
# brackets are there to help a human read it.
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")
# A value list is only a list of numbers and the words joining them.
_VALUE_LIST_RE = re.compile(r"^[\d\s,]*(?:(?:,|\bor\b|\band\b)\s*\d+\s*)*$", re.I)

_TOP_LEVEL_AND = re.compile(r"\bAND\b", re.I)
_TOP_LEVEL_OR = re.compile(r"\bOR\b", re.I)


def _split_top_level(text: str, pattern: re.Pattern) -> list[str] | None:
    """Split on `pattern` only where parentheses are balanced. None if never."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            m = pattern.match(text, i)
            if m:
                parts.append(text[start : m.start()])
                start = m.end()
                i = m.end()
                continue
        i += 1
    if not parts:
        return None
    parts.append(text[start:])
    return parts


def _atom(text: str) -> Expression | None:
    """One comparison, or None if it is not one we can read exactly."""
    found = _atom_on_question(text)
    if found is None:
        return None
    qid = _ATOM_RE.match(text).group(1)
    if "." in qid:
        name, row = qid.split(".", 1)
        _set_row(found, name, int(row))
    return found


def _set_row(expr: Expression, qid: str, row: int) -> None:
    if expr.qid:
        expr.qid, expr.row = qid, row
    for child in expr.children or []:
        _set_row(child, qid, row)


def _atom_on_question(text: str) -> Expression | None:
    m = _ATOM_RE.match(text)
    if not m:
        return None
    qid, op, raw_values = m.groups()
    raw_values = _PARENTHETICAL_RE.sub(" ", raw_values).strip()

    floor = _OPEN_RANGE_RE.match(raw_values.strip())
    if floor and op in ("=", "=="):
        # ">= n" is "not (x < n)" — the same ops the rest of the system reads.
        return Expression(
            op="not",
            children=[Expression(op="lt", qid=qid, value=int(floor.group(1)))],
            verbatim_text=text.strip(),
        )

    band = _RANGE_RE.match(raw_values.strip())
    if band and op in ("=", "=="):
        low, high = int(band.group(1)), int(band.group(2))
        if low > high:
            return None
        # Inclusive band, spelled with the ops that already exist everywhere
        # else: not(x < low) and not(x > high).
        return Expression(
            op="and",
            children=[
                Expression(op="not", children=[Expression(op="lt", qid=qid, value=low)]),
                Expression(op="not", children=[Expression(op="gt", qid=qid, value=high)]),
            ],
            verbatim_text=text.strip(),
        )

    if not _VALUE_LIST_RE.match(raw_values.strip()):
        return None
    values = [int(v) for v in _VALUES_RE.findall(raw_values)]
    if not values:
        return None

    if op in ("=", "=="):
        return Expression(op="in", qid=qid, values=values, verbatim_text=text.strip())
    if op in ("<>", "!="):
        # `not_in` is not a declared op — `not` over `in` is the same thing and
        # is what `evaluate()` and the SurveyJS compiler both already handle.
        return Expression(
            op="not",
            children=[Expression(op="in", qid=qid, values=values)],
            verbatim_text=text.strip(),
        )
    if len(values) != 1:
        # "D6 > 2005, 2006" is not a comparison anyone meant to write.
        return None
    value = values[0]
    if op == ">":
        return Expression(op="gt", qid=qid, value=value, verbatim_text=text.strip())
    if op == "<":
        return Expression(op="lt", qid=qid, value=value, verbatim_text=text.strip())
    # There is no ge/le op. "x >= n" is "not (x < n)", which evaluates the same
    # and stays inside the ops the rest of the system already understands.
    inner = "lt" if op == ">=" else "gt"
    return Expression(
        op="not",
        children=[Expression(op=inner, qid=qid, value=value)],
        verbatim_text=text.strip(),
    )


def _condition(text: str) -> Expression | None:
    text = text.strip()
    if not text:
        return None

    # One comparison FIRST. "ED0 = 3 or 4" is a single membership test whose
    # values happen to be joined by "or"; splitting on that "or" left " 4" as a
    # condition of its own and the whole base was thrown away. Parenthesised
    # lists ("(D1 = 3, 4, 5, OR 6)") only worked because the split ignores
    # anything inside brackets — the unparenthesised form is just as common.
    direct = _atom(text)
    if direct is not None:
        return direct

    has_and = _split_top_level(text, _TOP_LEVEL_AND)
    has_or = _split_top_level(text, _TOP_LEVEL_OR)
    if has_and and has_or:
        # "A AND B OR C" has no agreed precedence in a questionnaire margin.
        # Refuse rather than pick one and silently reroute the survey.
        return None
    for parts, op in ((has_and, "and"), (has_or, "or")):
        if not parts:
            continue
        children = [_condition(p) for p in parts]
        if any(c is None for c in children):
            return None
        return Expression(op=op, children=children, verbatim_text=text)

    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = _condition(stripped[1:-1])
        if inner is not None:
            return inner
    return _atom(text)


# "[ASK IF AWARE OF CVS (Q3=2)]" — a scripter explains the base in words and
# then writes the exact one in brackets. The parenthetical IS the condition.
_CONDITION_IN_PROSE = re.compile(r"\(([^()]*[A-Za-z][A-Za-z0-9]{0,5}\s*=\s*\d[^()]*)\)")


def parse_routing(directive: str) -> Expression | None:
    """The `show_if` a routing directive states, or None if it is not exact.

    `directive` is the text INSIDE the brackets, e.g. "IF S2 = 2 or S2=3".
    """
    if not directive:
        return None
    m = _PREAMBLE_RE.match(directive)
    if not m:
        return None
    body = directive[m.end() :]
    condition = _condition(body)
    if condition is None:
        # The exact condition may be parenthesised inside an explanation:
        # "AWARE OF CVS (Q3=2)". Only when the whole line does not parse, so a
        # condition that already reads correctly is never second-guessed.
        inner = _CONDITION_IN_PROSE.search(body)
        condition = _condition(inner.group(1)) if inner else None
    if condition is None:
        return None
    condition.verbatim_text = directive.strip()
    return condition
