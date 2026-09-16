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
    rf"\bASK\s+(?P<targets>{_QID}(?:\s*(?:-|–|—|,|\bto\b|\bthrough\b|\band\b)\s*{_QID})*)"
    rf"\s+(?P<cond>\bIF\b[^\]\n.]+)",
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
        for end in ends:
            if end in order:
                out.append(end)
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
            condition = parse_routing(match.group("cond"))
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
