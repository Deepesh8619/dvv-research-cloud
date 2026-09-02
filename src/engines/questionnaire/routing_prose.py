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
_PREAMBLE_RE = re.compile(r"^\s*(?:ONLY\s+ASK\b.*?|ASK\s+(?:ONLY\s+)?)?\bIF\b\s*", re.I)

# One comparison: a question id, an operator, then one or more numbers.
# The id is the same shape the segmenter recognises as a label (D2, S2, ED11A,
# PPT612) so a condition can only ever reference something that could be a
# question.
_ATOM_RE = re.compile(
    r"^\s*\(?\s*([A-Za-z]{1,4}\d+[A-Za-z]?)\s*(=|==|<>|!=|>=|<=|>|<)\s*([^)]*?)\s*\)?\s*$"
)
_VALUES_RE = re.compile(r"\d+")
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
    m = _ATOM_RE.match(text)
    if not m:
        return None
    qid, op, raw_values = m.groups()
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


def parse_routing(directive: str) -> Expression | None:
    """The `show_if` a routing directive states, or None if it is not exact.

    `directive` is the text INSIDE the brackets, e.g. "IF S2 = 2 or S2=3".
    """
    if not directive:
        return None
    m = _PREAMBLE_RE.match(directive)
    if not m:
        return None
    condition = _condition(directive[m.end() :])
    if condition is None:
        return None
    condition.verbatim_text = directive.strip()
    return condition
