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


# --- Routing written as a plain line, not a bracketed directive --------------
#
#     Base: All with at least one car (A1 > 0)
#     ASK THOSE WHO ANSWERED CODES a OR b AT Q4
#     TERMINATE INTERVIEW IF S1 = 1          SCREEN OUT IF Q1 = b
#     IF UNDER 16 - END SURVEY               IF Q10 = 2: TERMINATE & THANK
#
# Agencies that do not bracket their notes still write them as a short line of
# capitals beside the question. `loose_base` / `loose_terminate` read those with
# the same exact-or-nothing rule as `parse_routing`: a line we cannot read to a
# condition is still RECOGNISED (so it never becomes an answer option) but
# carries no expression.

_QID_TOKEN = r"[A-Za-z]{1,4}\d+[A-Za-z]?(?:_\d+)?|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9]"
_LOOSE_BASE_LABEL = re.compile(
    r"^\s*(?:base|filter|universe|asked\s+of|ask\s+(?:only\s+)?(?:if|those|all|everyone|"
    r"respondents)|show\s+(?:only\s+)?(?:if|to)|display\s+(?:only\s+)?if)\b\s*[:\-–—]?\s*",
    re.I,
)
_EVERYONE = re.compile(r"^(?:all|everyone|everybody|total|all\s+respondents|all\s+qualified"
                       r"(?:\s+respondents)?|all\s+qualifying\s+respondents)\s*\.?$", re.I)
# A comparison in brackets after a description: "(A1 > 0)", "(B1 = 1-6)".
_PAREN_CONDITION = re.compile(r"\(([^()]*\b(?:" + _QID_TOKEN + r")\s*(?:=|==|<>|!=|>=|<=|>|<)[^()]*\d[^()]*)\)")  # noqa: E501
# "answered code f at Q2", "selected codes a OR b OR c AT Q8", "who said 1-3 in Q5".
_CODED_AT = re.compile(
    r"^(?:those\s+)?(?:who\s+)?(?:answered|selected|chose|choosing|said|ticked|picked|coded|giving|gave)"
    r"\s+(?:(?:code|codes|option|options|answer|answers)\s+)?(?P<codes>[^'\"]+?)"
    r"\s+(?:at|in)\s+(?P<qid>" + _QID_TOKEN + r")\s*\.?$",
    re.I,
)
# "rating 4-5 at B13": the codes sit just before "at <question>".
_VALUES_AT = re.compile(
    r"^.*?\b(?P<codes>\d{1,3}(?:\s*[-–]\s*\d{1,3})?(?:\s*(?:,|\bor\b)\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?)*)"
    r"\s+(?:at|in)\s+(?P<qid>" + _QID_TOKEN + r")\s*\.?$",
    re.I,
)
_IN_LIST = re.compile(r"\s+in\s*\(\s*([\d,\s\-–]+?)\s*\)", re.I)
_CODE_SEP = re.compile(r"\s*(?:,|\bor\b|\band\b|/)\s*", re.I)
# "= f", "= a OR b": lettered answers, a=1 as the document's own header says.
_LETTER_VALUES = re.compile(r"(=\s*)([a-z](?:\s*(?:,|\bor\b)\s*[a-z])*)\s*(?=\)|$)", re.I)

_TERM = (r"(?:terminate|screen[\s-]?out|end(?:\s+(?:the\s+)?(?:survey|interview))?|"
         r"close(?:\s+(?:the\s+)?(?:survey|interview))?|thank\s+and\s+(?:close|end|terminate)|"
         r"disqualify|exit)")
_LOOSE_TERM_FIRST = re.compile(
    rf"^\s*[\[(]?\s*{_TERM}(?:\s+(?:the\s+)?(?:interview|respondents?|survey))?\s*(?:if|when|:)\s*(?P<cond>.+?)\s*[\])]?\s*\.?$",
    re.I,
)
_LOOSE_IF_FIRST = re.compile(
    rf"^\s*[\[(]?\s*if\s+(?P<cond>.+?)\s*(?:[:,;]|[-–—]+|→|->|=>)\s*(?:\w+\s+)?{_TERM}\b.*$",
    re.I,
)
# "<3 OR >35 YEARS", "<50%": a test on the question's OWN answer, no id written.
_RELATIVE = re.compile(r"^(<=|>=|<|>)\s*(\d+(?:\.\d+)?)\s*%?\s*(?:years?(?:\s+old)?)?$", re.I)
_UNDER_OVER = re.compile(r"^(?:respondent\s+is\s+|age\s+)?(under|below|over|above|less\s+than|more\s+than)\s+(\d+)\s*(?:years?(?:\s+old)?)?$", re.I)  # noqa: E501


def _letters_to_codes(text: str) -> str:
    def swap(m: re.Match) -> str:
        letters = re.findall(r"[a-z]", m.group(2), re.I)
        return m.group(1) + " or ".join(str(ord(c.lower()) - 96) for c in letters)

    return _LETTER_VALUES.sub(swap, text)


def _code_values(raw: str) -> list[int] | None:
    values: list[int] = []
    for token in _CODE_SEP.split(raw.strip()):
        token = token.strip().replace("–", "-")
        if re.fullmatch(r"[A-Za-z]", token):
            values.append(ord(token.lower()) - 96)
        elif m := re.fullmatch(r"(\d{1,3})\s*-\s*(\d{1,3})", token):
            low, high = int(m.group(1)), int(m.group(2))
            if high < low:
                return None
            values += range(low, high + 1)
        elif re.fullmatch(r"\d{1,3}", token):
            values.append(int(token))
        else:
            return None
    return values or None


def condition_in_line(text: str) -> Expression | None:
    """The exact condition a plain-prose line states, or None."""
    text = _IN_LIST.sub(r" = \1", _letters_to_codes(text.strip().rstrip(".")))
    found = _condition(text)
    if found is None and (inner := _PAREN_CONDITION.search(text)):
        found = _condition(_letters_to_codes(inner.group(1)))
    if found is None and (coded := _CODED_AT.match(text) or _VALUES_AT.match(text)):
        values = _code_values(coded.group("codes"))
        if values:
            found = Expression(op="in", qid=coded.group("qid"), values=values)
    return found


def loose_base(line: str) -> tuple[bool, Expression | None]:
    """(is a base line, its condition). "Base: All respondents" -> (True, None)."""
    text = (line or "").strip()
    m = _LOOSE_BASE_LABEL.match(text)
    if not m or len(text) > 220 or text.endswith("?"):
        return False, None
    body = text[m.end():].strip()
    if not body or _EVERYONE.match(body):
        return True, None
    found = condition_in_line(body)
    if found is not None:
        found.verbatim_text = text
    return True, found


def _bands_as_lists(expr: Expression, is_choice) -> Expression:
    """"B1 = 1-6" as `in [1..6]` when B1 is a choice question: the same test on
    one answer, and the right one on a multi-select, where "not below 1 and not
    above 6" is true of nothing."""
    expr.children = (
        [_bands_as_lists(c, is_choice) for c in expr.children] if expr.children else expr.children
    )
    kids = expr.children or []
    if (
        expr.op == "and" and len(kids) == 2
        and all(k.op == "not" and len(k.children or []) == 1 for k in kids)
    ):
        low, high = (k.children[0] for k in kids)
        if (
            low.op == "lt" and high.op == "gt" and low.qid == high.qid and is_choice(low.qid)
            and 0 <= high.value - low.value <= 40
        ):
            return Expression(op="in", qid=low.qid, values=list(range(low.value, high.value + 1)),
                              verbatim_text=expr.verbatim_text)
    return expr


def _relative(cond: str, own_qid: str) -> Expression | None:
    parts = re.split(r"\s+(OR|AND)\s+", cond.strip(), flags=re.I)
    tests = []
    for part in parts[::2]:
        m = _RELATIVE.match(part.strip())
        if not m and (w := _UNDER_OVER.match(part.strip())):
            word = w.group(1).lower()
            m = _RELATIVE.match(
                f"{'<' if word in ('under', 'below') or word.startswith('less') else '>'}{w.group(2)}"  # noqa: E501
            )
        if not m:
            return None
        op, number = m.group(1), float(m.group(2))
        number = int(number) if number.is_integer() else number
        if op in ("<", ">"):
            tests.append(Expression(op="lt" if op == "<" else "gt", qid=own_qid, value=number))
        else:  # ">=" is "not below", "<=" is "not above"
            inner = Expression(op="lt" if op == ">=" else "gt", qid=own_qid, value=number)
            tests.append(Expression(op="not", children=[inner]))
    if len(tests) == 1:
        return tests[0]
    joiner = "and" if parts[1].upper() == "AND" else "or"
    if any(j.upper() != joiner.upper() for j in parts[1::2]):
        return None
    return Expression(op=joiner, children=tests)


def loose_terminate(line: str, own_qid: str | None = None) -> tuple[bool, Expression | None]:
    """(is a screen-out line, its condition). "UNDER n" is a test on `own_qid`."""
    text = (line or "").strip()
    m = _LOOSE_TERM_FIRST.match(text) or _LOOSE_IF_FIRST.match(text)
    if not m or len(text) > 220 or text.endswith("?"):
        return False, None
    cond = m.group("cond").strip()
    found = None
    if own_qid and (found := _relative(cond, own_qid)) is not None:
        pass
    else:
        found = condition_in_line(cond)
    if found is not None:
        found.verbatim_text = text
    return True, found


def hoist_loose_routing(chunks: list) -> list:
    """Put plain-line routing in the chunk it governs.

    Segmentation cuts at question labels, so a line written ABOVE a question
    ("Base: All with a car") sits at the tail of the PREVIOUS chunk, where it
    was read as one more answer of that question; a screen-out written BELOW a
    question can land in a chunk of its own, where nothing reads it. This moves
    base lines forward to the next question and screen-out lines back to the
    question above, so `parse_prose_question` finds each where it belongs.

    Bases move only when the document writes them ABOVE its questions: the
    first base line in the document comes before the first question. A document
    that puts "Base:" under each question is left exactly as it is.
    """
    from dataclasses import replace

    def kind(line: str) -> str | None:
        if loose_base(line)[0]:
            return "base"
        return "term" if loose_terminate(line)[0] else None

    texts = [c.chunk_text.splitlines() for c in chunks]
    first_q = next((i for i, c in enumerate(chunks) if c.candidate_qid), None)
    if first_q is None:
        return chunks
    first_base = next(
        ((i, n) for i, lines in enumerate(texts) for n, ln in enumerate(lines)
         if kind(ln) == "base"),
        None,
    )
    if first_base is None and not any(kind(ln) for lines in texts for ln in lines):
        return chunks
    bases_above = first_base is not None and (
        first_base[0] < first_q
        or (first_base[0] == first_q and first_base[1] < _label_line(texts[first_q]))
    )

    moved_to: dict[int, list[str]] = {}   # chunk index -> lines to prepend
    appended: dict[int, list[str]] = {}   # chunk index -> lines to append
    removed: dict[int, set[int]] = {}
    for i, lines in enumerate(texts):
        is_question = bool(chunks[i].candidate_qid)
        # a question chunk: only its trailing run; any other chunk: every line
        start = len(lines)
        if is_question:
            while start > 0 and kind(lines[start - 1]):
                start -= 1
            # a screen-out written ABOVE a label tests the answer just given
            for n in range(_label_line(lines)):
                if kind(lines[n]) == "term":
                    dest = next((j for j in range(i - 1, -1, -1) if chunks[j].candidate_qid), None)
                    if dest is not None:
                        appended.setdefault(dest, []).append(lines[n])
                        removed.setdefault(i, set()).add(n)
        else:
            start = 0
        for n in range(start, len(lines)):
            what = kind(lines[n])
            if what == "base" and bases_above:
                dest = next((j for j in range(i + 1, len(chunks)) if chunks[j].candidate_qid), None)
                if dest is not None:
                    moved_to.setdefault(dest, []).append(lines[n])
                    removed.setdefault(i, set()).add(n)
            elif what == "term" and not is_question:
                dest = next((j for j in range(i - 1, -1, -1) if chunks[j].candidate_qid), None)
                if dest is not None:
                    appended.setdefault(dest, []).append(lines[n])
                    removed.setdefault(i, set()).add(n)
    if not removed:
        return chunks

    out = []
    for i, chunk in enumerate(chunks):
        if i not in removed and i not in moved_to and i not in appended:
            out.append(chunk)
            continue
        gone = removed.get(i, set())
        lines = moved_to.get(i, []) + [ln for n, ln in enumerate(texts[i]) if n not in gone] + appended.get(i, [])  # noqa: E501
        out.append(replace(chunk, chunk_text="\n".join(lines)))
    return out


def _label_line(lines: list[str]) -> int:
    """Index of the first line that is not routing: where the wording starts."""
    return next((n for n, ln in enumerate(lines) if not (loose_base(ln)[0] or loose_terminate(ln)[0])), 0)  # noqa: E501


# "IF A3 = 3 OR 4 SKIP TO A6": every question between this one and A6 is asked
# only of those the condition does not send past it.
_SKIP_LINE = re.compile(
    r"^\s*[\[(]?\s*if\s+(?P<cond>.+?)\s*[,:;-]?\s*(?:skip|go|jump)\s*(?:to|ahead\s+to)\s+"
    r"(?P<target>" + _QID_TOKEN + r")\s*[\])]?\s*\.?$",
    re.I,
)


def apply_skips(survey) -> None:
    """Gate the questions a stated skip jumps over. Minimal: reads each
    question's own source text, changes only `show_if` of questions in between."""
    order = {q.qid.upper(): i for i, q in enumerate(survey.questions)}
    for i, question in enumerate(survey.questions):
        for line in (question.source_text or "").splitlines():
            m = _SKIP_LINE.match(line.strip())
            if not m or m.group("target").upper() not in order:
                continue
            cond = condition_in_line(m.group("cond"))
            target = order[m.group("target").upper()]
            if cond is None or target <= i + 1:
                continue
            skipped = Expression(op="not", children=[cond], verbatim_text=line.strip())
            for between in survey.questions[i + 1 : target]:
                if between.show_if is None:
                    between.show_if = skipped
                elif skipped.verbatim_text not in (between.show_if.verbatim_text or ""):
                    between.show_if = Expression(
                        op="and", children=[between.show_if, skipped],
                        verbatim_text=between.show_if.verbatim_text,
                    )


# "FOR EACH BRAND CHOSEN AT B2 ASK B3 TO B5": B3-B5 are worded around a
# placeholder ("<BRAND>") that stands for one answer of B2.
_CAROUSEL_NOTE = re.compile(
    r"FOR\s+EACH\s+[A-Z ]{3,30}?\s+(?:CHOSEN|SELECTED|MENTIONED|PICKED)\s+(?:AT|IN)\s+"
    r"(?P<src>" + _QID_TOKEN + r")\s+ASK\s+(?P<first>" + _QID_TOKEN + r")\s*(?:TO|-|–|THROUGH)\s*"
    r"(?P<last>" + _QID_TOKEN + r")",
    re.I,
)
_PLACEHOLDER = re.compile(r"<\s*[A-Za-z][A-Za-z ]{1,20}>|\{\s*[A-Z][A-Z_ ]{1,20}\}")


def apply_placeholder_pipes(survey) -> None:
    order = {q.qid.upper(): i for i, q in enumerate(survey.questions)}
    for question in survey.questions:
        for note in _CAROUSEL_NOTE.finditer(question.source_text or ""):
            first, last = order.get(note.group("first").upper()), order.get(note.group("last").upper())  # noqa: E501
            if first is None or last is None:
                continue
            source = note.group("src").upper()
            for member in survey.questions[first : last + 1]:
                found = _PLACEHOLDER.search(member.text or "")
                if not found or source in {r.upper() for r in member.piping_refs}:
                    continue
                member.piping_refs.append(source)
                techniques = dict(member.validation.get("techniques") or {})
                techniques["pipes"] = [
                    *techniques.get("pipes", []),
                    {"source_qid": source, "name": None, "verbatim": found.group(0).strip("<>{} ")},
                ]
                member.validation["techniques"] = techniques


def apply_choice_bands(survey) -> None:
    """Every "Q = 1-6" band on a CHOICE question becomes a code list."""
    choice = {q.qid.upper() for q in survey.questions if q.options}
    for question in survey.questions:
        for name in ("show_if", "terminate_if"):
            expr = getattr(question, name)
            if expr is not None:
                setattr(question, name, _bands_as_lists(expr, lambda qid: qid.upper() in choice))
