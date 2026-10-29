"""Apply a section's routing line to the questions it names.

A recruitment path states the base for a WHOLE RANGE of questions, once, in a
header above them:

    Parent Recruitment Path Ages 26-60 (IF S0=1 AND S1=26-60) ASK P1-P3

Per-question extraction cannot act on that: the line lives in one chunk and the
questions it governs are in later ones, so it was read as free text and P1-P3
went out to everybody. This pass runs once the whole survey is assembled, when
"P1-P3" can actually be resolved against the real question order.

Two things come out of it, both of which a scripter would do by hand:

1. Every question in the range gets the condition as its base (AND-ed with its
   own, if it already has one — a question inside a path must satisfy both).
2. The path itself becomes a HID_ hidden variable, because that is what it is in
   the data file. Which path a respondent came down is the variable every banner
   is cut by ("Parent path" vs "Teen path"), and it is derived, never asked. The
   data writer already understands HID_/DERIVED_VARIABLE, so emitting one here
   puts the path in the data map instead of leaving each analyst to reconstruct
   it from S0 and S1 by hand.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire.routing_prose import parse_routing

_QID = r"[A-Za-z]{0,4}\d+[A-Za-z]?"
# "<path name> (IF ...) ASK P1-P3" / "ASK P1, P2, P3" / "ASK P1 THROUGH P3".
_SECTION_PATH_RE = re.compile(
    rf"(?P<name>[^()\n]{{0,80}}?)\(\s*(?P<cond>[^()]*?\bIF\b[^()]*?)\s*\)\s*"
    rf"[,:]?\s*ASK\s+(?P<targets>{_QID}(?:\s*(?:-|–|—|,|\bto\b|\bthrough\b|\band\b)\s*{_QID})*)",
    re.I,
)
_SPLIT_QID = re.compile(r"^([A-Z]{0,4})(\d+)([A-Z]?)$")
# "[PN: ASK Q15 IF Q14 = 1 OR 2]" — the same statement as a section path with
# the clauses the other way round, and written against a single question.
_FORWARD_ASK_RE = re.compile(
    rf"\b(?P<verb>ASK|SKIP)\s+(?P<targets>{_QID}(?:\s*(?:-|–|—|,|\bto\b|\bthrough\b|\band\b)\s*{_QID})*)"
    # "IF …", or "OF SPLIT A (S0a=1)": who, then the exact condition in brackets.
    # A dot ends the sentence, except inside "Q38.11" (row 11 of Q38).
    rf"\s+(?P<cond>\bIF\b(?:[^\]\n.]|\.(?=\d))+|\bOF\b(?:[^\]\n.]|\.(?=\d))+)",
    re.I,
)
_RANGE_SEP_RE = re.compile(r"\s*(?:-|–|—|\bto\b|\bthrough\b)\s*", re.I)

# "[PN: SHOW IF P1=1]" followed by the paragraph it governs — an intro screen
# shown only to part of the sample. The directive names its base; the text under
# it is what that base actually sees.
_INTRO_RE = re.compile(
    r"\[\s*(?:PN\s*:\s*)?((?:SHOW|DISPLAY|ASK)\s+(?:ONLY\s+)?IF\b[^\]]*)\]"
    r"(?P<body>(?:\n[^\n]+)+)",
    re.I,
)
_NEXT_LABEL_RE = re.compile(rf"^\s*{_QID}\s*[.):]")
_INTRO_MIN_CHARS = 60


_AT_QID = re.compile(r"\b(?:AT|IN|FOR|FROM)\s+([A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*)\b"
                     r"|\b([A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*)\s*=\s*YES\b", re.I)


def _exact_in_brackets(text: str) -> Expression | None:
    """ "OF SPLIT A (S0a=1)": the exact condition, in brackets."""
    inner = re.search(r"\(([^)]*=[^)]*)\)", text)
    return parse_routing(f"IF {inner.group(1)}") if inner else None


def apply_worded_asks(survey: SurveyDefinition) -> list[str]:
    """ "ASK Q12-Q19 IF EVER PURCHASED BUBBLE AT Q2b": the answer is named in
    words. Run LATE, once answer lists are final — earlier, a table's leftover
    cells were still answers and "Bubble" matched two of them."""
    order = survey.question_order()
    by_qid = {q.qid: q for q in survey.questions}
    asked_of_all = _asked_of_all(survey, order)
    gated: list[str] = []
    for question in survey.questions:
        for match in _FORWARD_ASK_RE.finditer(question.source_text or ""):
            if parse_routing(match.group("cond")) or _exact_in_brackets(match.group("cond")):
                continue  # exact: already applied by apply_section_paths
            condition = answer_named(match.group("cond"), by_qid)
            if condition is None:
                continue
            if match.group("verb").upper() == "SKIP":
                condition = Expression(op="not", children=[condition],
                                       verbatim_text=condition.verbatim_text)
            for qid in _targets(match.group("targets"), order):
                target = by_qid.get(qid)
                if (target is None or qid == question.qid or target.show_if is not None
                        or qid in asked_of_all):
                    continue
                target.show_if = Expression.from_dict(condition.to_dict())
                target.extraction_evidence.setdefault("base", f"[{match.group(0).strip()}]")
                gated.append(qid)
    return gated


_ASK_ALL_RE = re.compile(
    r"\bASK\s+(?P<targets>[A-Za-z][\w.]*\d[\w.]*(?:\s*[-–,]\s*[A-Za-z][\w.]*\d[\w.]*)*)"
    # "OF ALL" must END the note: "ASK Q16 OF ALL EVER PURCHASED …" is a base.
    r"\s+(?:OF|TO)\s+ALL(?:\s+RESPONDENTS)?\s*(?:[\].;)]|$)", re.I | re.M)


def _asked_of_all(survey: SurveyDefinition, order: list[str]) -> set[str]:
    """Questions the document says everyone answers: "[PN: ASK Q19 OF ALL]".

    Inside "ASK Q12-Q19 IF EVER PURCHASED …" this exempts Q19; gating it
    anyway reported 1,030 of 1,541 Bubble respondents as answering outside
    their base.
    """
    out: set[str] = set()
    for question in survey.questions:
        for match in _ASK_ALL_RE.finditer(question.source_text or ""):
            out.update(_targets(match.group("targets"), order))
    return out


def answer_named(text: str, by_qid: dict) -> Expression | None:
    """ "IF EVER PURCHASED BUBBLE AT Q2b" / "Q2b = YES FOR BUBBLE": the answer is
    named in words, at a question named by id. Exactly ONE of that question's
    answers must appear in the words, as a whole word; otherwise None — two
    candidates is a guess, and a person decides."""
    inner = re.search(r"\(([^)]*=[^)]*)\)", text)
    if text.upper().lstrip().startswith("OF") and inner:
        exact = parse_routing(f"IF {inner.group(1)}")
        if exact is not None:
            return exact
    found = _AT_QID.search(text)
    if not found:
        return None
    name = found.group(1) or found.group(2)
    upper = {k.upper(): k for k in by_qid}
    source = by_qid.get(upper.get(name.upper(), ""))
    if source is None:
        return None
    words = text.lower()
    hits = [o for o in source.options or []
            if len((o.label or "").strip()) >= 3
            and re.search(rf"\b{re.escape(o.label.strip().lower())}\b", words)]
    if len(hits) != 1:
        return None
    return Expression(op="in", qid=source.qid, values=[hits[0].code], verbatim_text=text.strip())


def _targets(spec: str, order: list[str]) -> list[str]:
    """The qids "P1-P3" or "P1, P2, P4" names, in document order.

    A range is resolved against the REAL order, never by counting: "P1-P3" in a
    document that also has P2A means four questions, and generating names would
    have missed it.
    """
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        ends = [e.strip().upper() for e in _RANGE_SEP_RE.split(part) if e.strip()]
        if len(ends) == 2:
            band = _band(ends[0], ends[1], order)
            if band:
                out.extend(band)
                continue
        # "ASK Q29a …": the name is upper-cased for the range logic, the
        # question is Q29a — matched exactly, it was never found.
        by_upper = {qid.upper(): qid for qid in order}
        for end in ends:
            if end in by_upper:
                out.append(by_upper[end])
    return list(dict.fromkeys(out))


def _band(low: str, high: str, order: list[str]) -> list[str]:
    """Every qid in "P1-P3", by prefix and number — not by position.

    Numbers, because an end of the range may not have been extracted at all
    ("ASK P1-P3" in a document where P1 failed to parse), and slicing between
    two positions would then silently route nothing. It also picks up P2A, which
    is inside the range the scripter meant and which generating names would miss.
    """
    lo, hi = _SPLIT_QID.match(low), _SPLIT_QID.match(high)
    if not (lo and hi) or lo.group(1) != hi.group(1):
        if low in order and high in order and order.index(low) <= order.index(high):
            return order[order.index(low) : order.index(high) + 1]
        return []
    first, last = int(lo.group(2)), int(hi.group(2))
    if first > last:
        return []
    picked = []
    for qid in order:
        m = _SPLIT_QID.match(qid.upper())
        if m and m.group(1) == lo.group(1) and first <= int(m.group(2)) <= last:
            picked.append(qid)
    return picked


def _hidden_qid(name: str, taken: set[str]) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:40] or "PATH"
    qid = f"HID_{slug}"
    suffix = 2
    while qid in taken:
        qid, suffix = f"HID_{slug}_{suffix}", suffix + 1
    return qid


def apply_section_paths(survey: SurveyDefinition) -> list[dict]:
    """Route every section path found in the questions' own source text.

    Returns one record per applied path (for `survey.metadata`, so a reviewer
    sees what was routed and by what line). Mutates `survey`.
    """
    order = survey.question_order()
    by_qid = {q.qid: q for q in survey.questions}
    applied: list[dict] = []
    seen_lines: set[str] = set()

    for question in list(survey.questions):
        for match in _FORWARD_ASK_RE.finditer(question.source_text or ""):
            condition = parse_routing(match.group("cond")) or _exact_in_brackets(
                match.group("cond")
            )
            if condition is not None and match.group("verb").upper() == "SKIP":
                condition = Expression(op="not", children=[condition],
                                       verbatim_text=condition.verbatim_text)
            targets = [
                qid
                for qid in _targets(match.group("targets"), order)
                if qid != question.qid and by_qid.get(qid) and by_qid[qid].show_if is None
            ]
            if condition is None or not targets:
                continue
            line = match.group(0).strip()
            for qid in targets:
                by_qid[qid].show_if = _both(by_qid[qid].show_if, condition)
                by_qid[qid].extraction_evidence.setdefault("base", f"[{line}]")
            applied.append(
                {"path": "", "hidden_qid": "", "questions": targets, "source": line}
            )

        for match in _SECTION_PATH_RE.finditer(question.source_text or ""):
            line = match.group(0).strip()
            if line in seen_lines:
                continue
            condition = parse_routing(match.group("cond"))
            if condition is None:
                continue
            targets = [
                qid for qid in _targets(match.group("targets"), order) if qid != question.qid
            ]
            if not targets:
                continue
            seen_lines.add(line)

            name = match.group("name").strip(" .:-–—") or "Recruitment path"
            hidden = _hidden_variable(name, condition, line, set(by_qid))
            by_qid[hidden.qid] = hidden
            _insert_before(survey, hidden, targets[0])

            for qid in targets:
                target = by_qid.get(qid)
                if target is None:
                    continue
                target.show_if = _both(target.show_if, condition)
                target.extraction_evidence.setdefault("base", f"[{line}]")

            applied.append(
                {"path": name, "hidden_qid": hidden.qid, "questions": targets, "source": line}
            )

    if applied:
        survey.metadata["section_paths"] = applied
    return applied


def apply_intro_screens(survey: SurveyDefinition) -> list[dict]:
    """Turn "[PN: SHOW IF P1=1] <paragraph>" into the screen it describes.

    The paragraph sits inside the PREVIOUS question's chunk, so extraction had
    nowhere to put it — it used to be read as an extra answer option of that
    question. It is its own screen, shown to a subset, and it has to exist as
    one or the respondent who qualifies never sees it.
    """
    added: list[dict] = []
    taken = {q.qid for q in survey.questions}

    for question in list(survey.questions):
        for match in _INTRO_RE.finditer(question.source_text or ""):
            condition = parse_routing(match.group(1))
            if condition is None:
                continue
            body = _intro_body(match.group("body"))
            if not body or _already_present(survey, body):
                continue
            qid = f"INTRO_{question.qid}"
            suffix = 2
            while qid in taken:
                qid, suffix = f"INTRO_{question.qid}_{suffix}", suffix + 1
            taken.add(qid)
            screen = Question(
                qid=qid,
                text=body,
                type=QuestionType.INFO,
                required=False,
                show_if=condition,
                source_text=match.group(0).strip(),
                extraction_method="intro_screen",
                extraction_evidence={"base": f"[{match.group(1).strip()}]"},
            )
            _insert_after(survey, screen, question.qid)
            added.append({"qid": qid, "after": question.qid, "base": match.group(1).strip()})

    if added:
        survey.metadata["intro_screens"] = added
    return added


def _intro_body(raw: str) -> str:
    """The paragraph under the directive, stopping at the next question label."""
    kept: list[str] = []
    for line in raw.strip("\n").splitlines():
        line = line.strip()
        if not line:
            break
        if _NEXT_LABEL_RE.match(line) or not any(c.islower() for c in line):
            break
        kept.append(line)
    body = " ".join(kept).strip()
    return body if len(body) >= _INTRO_MIN_CHARS else ""


def _already_present(survey: SurveyDefinition, body: str) -> bool:
    head = body[:60].lower()
    return any((q.text or "").lower().startswith(head) for q in survey.questions)


def _insert_after(survey: SurveyDefinition, question: Question, qid: str) -> None:
    index = next((i for i, q in enumerate(survey.questions) if q.qid == qid), -1)
    survey.questions.insert(index + 1 if index >= 0 else len(survey.questions), question)
    survey._rebuild_index()
    for block in survey.blocks:
        if qid in block.question_ids:
            block.question_ids.insert(block.question_ids.index(qid) + 1, question.qid)
            return
    if survey.blocks:
        survey.blocks[-1].question_ids.append(question.qid)


def _both(existing: Expression | None, section: Expression) -> Expression:
    """A question's own base AND its section's — never a replacement.

    P2 asked only of parents who said yes at P1 is still only asked inside the
    parent path. Dropping either half sends the question to people the script
    never intended to ask.
    """
    if existing is None:
        return section
    if existing.op == "and" and any(
        c.verbatim_text == section.verbatim_text for c in existing.children or []
    ):
        return existing
    return Expression(
        op="and",
        children=[existing, section],
        verbatim_text=" AND ".join(
            t for t in (existing.verbatim_text, section.verbatim_text) if t
        ),
    )


def _hidden_variable(name: str, condition: Expression, line: str, taken: set[str]) -> Question:
    return Question(
        qid=_hidden_qid(name, taken),
        text=name,
        type=QuestionType.INFO,
        required=False,
        show_if=condition,
        source_text=line,
        flags=[schema.FLAG_DERIVED_VARIABLE],
        extraction_method="section_path",
        extraction_evidence={"base": f"[{line}]"},
    )


def _insert_before(survey: SurveyDefinition, question: Question, qid: str) -> None:
    index = next(
        (i for i, q in enumerate(survey.questions) if q.qid == qid), len(survey.questions)
    )
    survey.questions.insert(index, question)
    survey._rebuild_index()
    for block in survey.blocks:
        if qid in block.question_ids:
            block.question_ids.insert(block.question_ids.index(qid), question.qid)
            return
    if survey.blocks:
        survey.blocks[-1].question_ids.append(question.qid)


# "BUBBLE CRM INTRODUCTION & SCREENER (S0=2)": a whole line that is a section's
# name and the one condition everybody in it meets — no "ASK <range>".
_SECTION_BASE_RE = re.compile(
    rf"^\s*(?P<name>[^()\n]{{4,90}}?)\s*\(\s*(?P<cond>{_QID}\s*=\s*\d[\d\s,\-–]*)\)\s*$"
)
_ALL_HEADER_RE = re.compile(r"\(\s*ALL(?:\s+RESPONDENTS)?\s*\)\s*$", re.I)
_PREFIX = re.compile(r"[A-Za-z]*")
_NOTE_LINE = re.compile(r"^\s*(?:\[|NOTE\b|PN\b|PROGRAMMER|SCRIPTER)", re.I)


def _is_screen_id(question: Question) -> bool:
    """An information screen the reader made from the document's own words."""
    return question.type is QuestionType.INFO and question.qid.startswith(("INFO_", "INTRO"))


def apply_section_bases(survey: SurveyDefinition) -> list[str]:
    """Give each question under a "<SECTION> (S0=2)" heading that base.

    The section runs from the heading to the next such heading or an "(ALL
    RESPONDENTS)" heading, and only over questions sharing the first one's
    name prefix (B1, B2, … B9 — then C2 is another section). Without the
    prefix stop, a document with no closing heading would gate the rest of
    the survey on the screener's condition. Returns the qids it gated.
    """
    questions = survey.questions
    starts: list[tuple[int, Expression | None, str]] = []
    for i, question in enumerate(questions):
        lines = [ln.strip() for ln in (question.source_text or "").splitlines() if ln.strip()]
        for j, line in enumerate(lines):
            at = i if j == 0 else i + 1  # a heading under a question governs the next one
            match = _SECTION_BASE_RE.match(line)
            if match and _NOTE_LINE.match(line):
                continue  # "NOTE TO PROGRAMMER: … (S0=2)" explains, it does not open a section
            # A heading is written in capitals; "No Quota for Bubble CRM (S0=2)"
            # and "All Other (S11=3)" are table cells that happen to look alike.
            if match and any(c.islower() for c in match.group("name")):
                continue
            if match and not _SECTION_PATH_RE.search(line):
                starts.append((at, parse_routing(f"IF {match.group('cond')}"), line))
            elif _ALL_HEADER_RE.search(line) and len(line) < 120:
                starts.append((at, None, line))
    starts.sort(key=lambda s: s[0])
    starts = [s for n, s in enumerate(starts) if n == 0 or s[0] != starts[n - 1][0]]
    gated: list[str] = []
    for n, (start, condition, line) in enumerate(starts):
        if condition is None or start >= len(questions):
            continue
        stop = starts[n + 1][0] if n + 1 < len(starts) else len(questions)
        # The section's name prefix comes from its first QUESTION: an information
        # screen right under the heading ("Thank you for agreeing…") is part of the
        # section — it is shown only to that section's respondents — but its
        # INFO_/INTRO id must not end the section before S1 is reached.
        first = next((k for k in range(start, stop) if not _is_screen_id(questions[k])), None)
        if first is None:
            continue
        prefix = _PREFIX.match(questions[first].qid).group(0).upper()
        for question in questions[start:stop]:
            if _is_screen_id(question):
                condition_copy = Expression.from_dict(condition.to_dict())
                condition_copy.verbatim_text = condition_copy.verbatim_text or line
                question.show_if = _both(question.show_if, condition_copy)
                gated.append(question.qid)
                continue
            if _PREFIX.match(question.qid).group(0).upper() != prefix:
                break
            if schema.FLAG_DERIVED_VARIABLE in question.flags:
                continue
            condition_copy = Expression.from_dict(condition.to_dict())
            condition_copy.verbatim_text = condition_copy.verbatim_text or line
            question.show_if = _both(question.show_if, condition_copy)
            question.extraction_evidence.setdefault("base", f"[{line}]")
            gated.append(question.qid)
    return gated
