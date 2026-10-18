"""Exact consistency checks: does the instrument agree with itself?

Every check here compares two things the DOCUMENT states and finds they cannot
both be true (a derivation names a question that is not there, a directive
anchors a code the list does not have). No model, no guessing, no scoring: a
check either holds its evidence in hand or stays silent.

Two channels, matching how QA already reports:

* `consistency_check` -> `Issue`s that go through `qa_check`, so they raise
  QA_ISSUE and a person looks.
* `consistency_notes` -> informational `Issue`s. They never flag or block; they
  are kept on the question (`validation.consistency_notes`) and in the QA record.

Deliberately NOT checked: gaps in code numbers (a list may skip 4 by design), and
whether a derivation's label contradicts its own condition.
"""

from __future__ import annotations

import re
from collections import Counter

from src.core.survey_definition import GRID_TYPES, Question, QuestionType
from src.engines.questionnaire.qa_loop import Issue

# A question name as a derivation writes it: DA_USAGE, SS_EMP, Q2c, S6. Needs an
# underscore or a digit, so "NOT", "AND", "ONLY" are never read as questions.
_NAME = re.compile(r"\b[A-Z][A-Za-z]*[0-9_][A-Za-z0-9_]*(?:\.\d+)?")
# "SS_EMP = 3-4", "DA_USAGE 1, 2, 3, or 4 = 3-5": name, optional grid rows, codes.
_TEST = re.compile(
    r"(?P<name>[A-Z][A-Za-z]*[0-9_][A-Za-z0-9_]*)(?:\.\d+)?"
    r"(?P<rows>(?:\s*(?:,|\bor\b|\band\b)?\s*\d+)*)\s*=\s*"
    r"(?P<codes>\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)",
    re.I,
)
_RESIDUE = re.compile(r"^\s*(?:Q\s*Name|Response\s+list|Show\s*/\s*HV)\b", re.I)
_OPT_OUT = re.compile(r"prefer\s+not|don'?t\s+know|not\s+sure|refus|none\s+of", re.I)

_SELECT_ALL = re.compile(
    r"\b(?:select|check|mark|choose|tick|indicate)\s+all\b|\ball\s+that\s+apply\b", re.I
)
# "Anchor 98", "anchor 98 and 99", "Make 99 exclusive", "exclusive 99".
_DIRECTIVE = re.compile(
    r"\b(?P<verb>anchor|make|exclusive)\s+(?P<codes>\d{1,3}(?:(?:\s*,\s*|\s+and\s+)\d{1,3})*)"
    r"(?:\s+exclusive\b)?",
    re.I,
)
_NOTE_LABEL = re.compile(
    r"^\s*\[?\s*(?:PN|PROGRAMM(?:ER|ING)(?:\s+NOTE)?|SCRIPTER)\b", re.I
)
_NOTE_VERBS = (
    "RANDOMIZE|RANDOMISE|ROTATE|ENABLE|DISABLE|ANCHOR|PIPE|INSERT|SHOW|ASK|TERMINATE|"
    "SKIP|DISPLAY|FLIP|HIDE|REVERSE|KEEP|SORT|PROGRAM"
)
_NOTE_ALLCAPS = re.compile(rf"^(?:{_NOTE_VERBS})\b[^a-z]*\s[^a-z]*$")
_NOTE_BRACKETED = re.compile(rf"^\[(?:{_NOTE_VERBS})\b[^\]]*\]$", re.I)
_INLINE_IF = re.compile(r"\[\s*IF\b[^\]:]*:\s*([^\]]+)\]", re.I)
_ODD_QID = re.compile(r"[^A-Za-z0-9_.\-]")
_RANGE_DIRECTIVE = re.compile(
    r"\b(?:ENTER|RECORD|ALLOW)\s+(?:NUMBER\s+)?(\d+)\s*(?:-|TO)\s*(\d+)", re.I
)
_PERCENT = re.compile(r"\bpercent|%|\bproportion\b|\bshare\b", re.I)


def _codes_of(q: Question) -> set[int]:
    every = (q.options or []) + (q.rows or []) + (q.cols or [])
    return {o.code for o in every if o.code is not None}


def _expand(spec: str) -> set[int]:
    """'3-5' -> {3,4,5}; '1,2' -> {1,2}."""
    out: set[int] = set()
    for part in spec.split(","):
        bits = [int(b) for b in re.split(r"\s*[-–]\s*", part.strip()) if b.strip().isdigit()]
        if len(bits) == 2:
            out |= set(range(bits[0], bits[1] + 1))
        elif bits:
            out.add(bits[0])
    return out


def _resolve(name: str, by_qid: dict[str, Question]) -> Question | None:
    """The question a derivation means. 'DA_USAGE5' (a glued row number) counts."""
    base = name.split(".")[0]
    return by_qid.get(base) or by_qid.get(base.rstrip("0123456789"))


def _is_hidden(q: Question) -> bool:
    return q.qid.startswith(("HID_", "HIDDEN_")) or "DERIVED_VARIABLE" in (q.flags or [])


# --- 1. Hidden-variable derivations ------------------------------------------
def hidden_variable(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """HID_*: names that do not exist, codes the source never offers, codes in no group."""
    if not _is_hidden(q):
        return []
    issues: list[Issue] = []
    missing: dict[str, list[str]] = {}  # name -> options naming it
    bad_codes: list[str] = []
    groups: dict[str, list[tuple[str, set[int]]]] = {}  # source qid -> [(label, codes)]
    for o in q.options or []:
        text = o.definition or ""
        for token in _NAME.findall(text):
            if token.split(".")[0] != q.qid and _resolve(token, by_qid) is None:
                # "DA_USAGE5" is DA_USAGE with a row number stuck to it.
                name = token.rstrip("0123456789") if "_" in token.rstrip("0123456789") else token
                missing.setdefault(name, []).append(o.label[:30])
        for m in _TEST.finditer(text):
            src = _resolve(m["name"], by_qid)
            if src is None or src.qid == q.qid:
                continue
            wanted = _expand(m["codes"])
            if src.type in GRID_TYPES or src.rows:
                offered, label = {c.code for c in src.cols or []}, "scale"
                rows = {int(r) for r in re.findall(r"\d+", m["rows"])}
                gone = rows - {r.code for r in src.rows or []}
                if gone and not m["name"].lower().endswith(tuple("0123456789")):
                    bad_codes.append(f'"{o.label[:30]}": {src.qid} has no row {sorted(gone)}')
            else:
                offered, label = {c.code for c in src.options or []}, "answers"
            if offered and wanted - offered:
                bad_codes.append(
                    f'"{o.label[:30]}" tests {src.qid} = {sorted(wanted - offered)}, '
                    f"which is not among its {label} {sorted(offered)}"
                )
            is_grid = bool(src.rows) or src.type in GRID_TYPES
            if m.group(0).strip() == text.strip() and not is_grid:
                groups.setdefault(src.qid, []).append((o.label, wanted))
    for name, where in missing.items():
        issues.append(Issue(
            "HID_UNKNOWN_QUESTION",
            f"{q.qid} is derived from {name}, but the questionnaire has no question "
            f"called {name} (used by {len(where)} of its values)",
            f"{name}: " + "; ".join(where[:3]),
        ))
    for line in bad_codes:
        issues.append(Issue(
            "HID_UNKNOWN_CODE",
            f"{q.qid} derivation tests a code that is not offered: {line}",
            line[:160],
        ))
    for source, parts in groups.items():
        src = by_qid[source]
        if len(parts) < 2:
            continue
        covered = set().union(*(c for _, c in parts))
        labels = {o.code: o.label for o in src.options or []}
        # An opt-out ("Prefer not to answer") is rightly in no group, and a list
        # that carries table residue is not one to reason from.
        if any(_RESIDUE.search(label) for label in labels.values()):
            continue
        loose = sorted(c for c in labels if c not in covered and not _OPT_OUT.search(labels[c]))
        if loose:
            shown = ", ".join(f"{c} ({labels[c][:28]})" for c in loose)
            issues.append(Issue(
                "HID_CODE_IN_NO_GROUP",
                f"{q.qid} sorts respondents by {source}, but these {source} answers fall in "
                f"no group, so those respondents get no value: {shown}",
                f"groups cover {sorted(covered)}",
            ))
        tally = Counter(c for _, cs in parts for c in cs)
        both = sorted(c for c, n in tally.items() if n > 1)
        if both:
            issues.append(Issue(
                "HID_CODE_IN_TWO_GROUPS",
                f"{q.qid} puts {source} answer(s) {both} in more than one group",
                f"groups cover {sorted(covered)}",
            ))
    return issues


# --- 2. 'Select all' on a single-answer question ----------------------------
def select_all_on_single(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    if q.type not in (QuestionType.SINGLE, QuestionType.DROPDOWN):
        return []
    m = _SELECT_ALL.search(re.sub(r"\[[^\]]*\]", " ", q.text or ""))
    if not m:
        return []
    return [Issue(
        "SELECT_ALL_ON_SINGLE",
        f'{q.qid} is a single-answer question but its wording says "{m.group(0)}" '
        "- should it allow several answers?",
        (q.text or "")[:100],
    )]


# --- 3. Anchor / exclusive directive naming a code the list lacks ------------
def directive_names_missing_code(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    offered = _codes_of(q)
    if not offered:
        return []
    issues, seen = [], set()
    more = "..." if len(offered) > 12 else ""
    for m in _DIRECTIVE.finditer(q.source_text or ""):
        if m["verb"].lower() == "make" and "exclusive" not in m.group(0).lower():
            continue
        for code in (int(c) for c in re.findall(r"\d+", m["codes"])):
            if code not in offered and (m["verb"].lower(), code) not in seen:
                seen.add((m["verb"].lower(), code))
                issues.append(Issue(
                    "DIRECTIVE_CODE_MISSING",
                    f'{q.qid}: the document says "{m.group(0).strip()}" but the list has no '
                    f"code {code} (it offers {sorted(offered)[:12]}{more})",
                    m.group(0).strip(),
                ))
    return issues


# --- 4. Conditional wording hidden in a note ---------------------------------
def conditional_wording_dropped(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """'[IF C1=13-17: (or had someone purchase them for you)]' - the link strips the
    whole bracket, so the wording the document gives that group is lost.

    "[ASK OF ALL]" and friends are NOT reported: the link already hides them from the
    respondent, and QA judges the compiled screen. Reporting them cried wolf.
    """
    m = _INLINE_IF.search(q.text or "")
    if not m or "(" not in m.group(1) and len(m.group(1).split()) < 3:
        return []
    return [Issue(
        "CONDITIONAL_WORDING_DROPPED",
        f"{q.qid}: the document adds wording for some respondents ({m.group(0)[:100]}); the "
        "survey shows the plain question to everyone",
        m.group(0)[:120],
    )]


# --- 5. Programmer note read as an answer ------------------------------------
def note_as_answer(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    issues = []
    for where, items in (("answer", q.options), ("row", q.rows), ("column", q.cols)):
        for o in items or []:
            label = (o.label or "").strip()
            if _NOTE_LABEL.match(label) or _NOTE_BRACKETED.match(label) or (
                _NOTE_ALLCAPS.match(label) and len(label.split()) >= 2
            ):
                issues.append(Issue(
                    "NOTE_READ_AS_ANSWER",
                    f'{q.qid} offers a programmer note as {where} {o.code}: "{label[:60]}"',
                    label[:80],
                ))
    return issues


# --- 6. A reused question name that was renamed ------------------------------
def renamed_duplicate(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    original = (q.validation or {}).get("original_qid")
    if not original or original == "UNKNOWN":
        return []
    if q.type is QuestionType.INFO:
        # An information screen has no data column and nothing routes on it:
        # its new name changes nothing anyone has to check.
        return []
    return [Issue(
        "QID_RENAMED",
        f"the document names two questions {original}; this later one was renamed {q.qid} - "
        f"check routing and the data column that should point at it",
        f"{original} -> {q.qid}",
    )]


# --- 7. Odd characters in a question name ------------------------------------
def odd_qid(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    bad = sorted(set(_ODD_QID.findall(q.qid)))
    if not bad:
        return []
    return [Issue(
        "QID_ODD_CHARACTER",
        f'the question name "{q.qid}" contains {" ".join(repr(c) for c in bad)}; '
        "probably a typo that would break its data column",
        q.qid,
    )]


# --- 8. Number box with no range (INFO only) ---------------------------------
def numeric_without_range(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    v = q.validation or {}
    if q.type is not QuestionType.NUMERIC or q.rows or "min" in v or "max" in v or "sum" in v:
        return []
    if set(q.flags or []) & {"EXTRACTION_UNAVAILABLE", "EXTRACTION_MODEL_ERROR"}:
        return []
    text = " ".join([q.text or ""] + [str(d) for d in v.get("scripter_directives") or []])
    stated = _RANGE_DIRECTIVE.search(text)
    if stated:
        why = f'the document says "{stated.group(0)}" - suggest min {stated[1]}, max {stated[2]}'
    elif re.search(r"\bzip\b", q.text or "", re.I):
        why = "a ZIP code is exactly 5 digits - suggest requiring that"
    elif _PERCENT.search(q.text or ""):
        why = "this reads as a percentage - suggest 0 to 100"
    else:
        why = "no allowed range is set"
    return [Issue("NUMERIC_NO_RANGE", f"{q.qid} accepts any number: {why} (suggestion only)", why)]


# --- Screen-out answer that can be ticked with others ------------------------
def terminating_answer_not_exclusive(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """ "None of the above [ANCHOR, TERMINATE]" in a select-all question, not
    exclusive: a respondent can tick it with real answers, and is then screened
    out for an answer they contradicted."""
    if q.type is not QuestionType.MULTI or q.terminate_if is None:
        return []
    screened = set()
    if q.terminate_if.op == "in" and q.terminate_if.qid == q.qid:
        screened = set(q.terminate_if.values or [])
    # Only an opt-out ("None of the above", "Don't know", "No, I am not …"):
    # a screening industry ("Advertising") is ticked WITH others by design.
    loose = [o for o in q.options or [] if o.code in screened and not o.exclusive
             and (_OPT_OUT.search(o.label or "") or re.match(r"\s*no\b", o.label or "", re.I))]
    if not loose or len(loose) == len(q.options or []):
        return []
    return [Issue(
        "TERMINATING_ANSWER_NOT_EXCLUSIVE",
        f"{q.qid} screens out on {', '.join(str(o.code) for o in loose)} in a select-all list, "
        "but those answers are not exclusive - a respondent can tick them with others",
        "; ".join(o.label[:40] for o in loose),
    )]


# --- 10. A reading the document itself contradicts (tripwires) ----------------
# A question can be read confidently and wrongly: Bubble Q41 came out as a grid
# whose rows were "0=Agree fully with" and "Theme" — the scale's end label and a
# column header — with the six statement pairs gone and the 0-10 scale cut at 7.
# Nothing flagged it, so the second look and the model never saw it. These
# compare the reading against what the source text plainly shows.
# "1=Within the past 3 months", or a wrapped scale line carrying the next
# point: "6 months (but not in the past 3 months) | 3=Within the past".
_ANCHOR_ROW = re.compile(r"^\s*\d{1,2}\s*=|\|\s*\d{1,2}\s*=")
_HEADER_ROW = re.compile(
    r"^\s*(?:themes?|statements?|attributes?|items?|rows?|columns?)\s*(?:\[[^\]]*\])?\s*$", re.I
)
# A scale point opening a table cell (or a line inside one): "1", "5=Neutral".
_SCALE_POINT = re.compile(r"(?:^|\n)\s*(\d{1,2})\s*(?:=|$)", re.M)


def _row_lines(q: Question) -> list[str]:
    """Table rows the document names after the question: "Q41_1 | …", "Q41r2 | …"."""
    stem = re.escape(q.qid)
    pattern = re.compile(rf"^\s*{stem}(?:_|\.|r)?\d+\s*\|", re.I | re.M)
    return pattern.findall(q.source_text or "")


def _document_scale_top(q: Question) -> int | None:
    """The highest point of a scale the document writes as a run of table cells."""
    top, run = None, []
    for cell in (q.source_text or "").split("|"):
        m = _SCALE_POINT.search(cell)
        if m:
            run.append(int(m.group(1)))
            if len(run) >= 4:
                top = max(top or 0, *run)
        else:
            run = []
    return top


def is_junk_row(label: str, q: Question) -> bool:
    """A grid row that is a scale label or a column heading, not a thing to rate."""
    label = label or ""
    return bool(
        _ANCHOR_ROW.search(label) or _HEADER_ROW.match(label)
        or label.strip().lower() in {(c.label or "").strip().lower() for c in q.cols or []}
    )


def reading_contradicts_document(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    issues = []
    rows = q.rows or []
    junk = [r.label for r in rows if is_junk_row(r.label, q)]
    if junk:
        issues.append(Issue(
            "GRID_ROWS_NOT_STATEMENTS",
            f"{q.qid} has rows that are a scale label or a column heading, not things to "
            f"rate: {', '.join(repr(j[:40]) for j in junk[:3])}",
            "; ".join(j[:40] for j in junk[:3]),
        ))
    listed = _row_lines(q)
    if len(listed) >= 2 and len(listed) != len(rows):
        if q.type in GRID_TYPES or rows:
            detail = (f"the document lists {len(listed)} rows for {q.qid}; "
                      f"the reading has {len(rows)}")
        else:
            detail = (f"the document writes {q.qid} as a table of {len(listed)} rows; "
                      f"it was read as {q.type.value.replace('_', ' ')}")
        issues.append(Issue("ROWS_DIFFER_FROM_DOCUMENT", detail, listed[0].strip()[:60]))
    top = _document_scale_top(q)
    codes = [c.code for c in q.cols or [] if isinstance(c.code, int)]
    if q.cols and top is not None and codes and max(codes) < top:
        issues.append(Issue(
            "SCALE_CUT_SHORT",
            f"the document's scale for {q.qid} runs to {top}; the reading stops at {max(codes)}",
            f"0-{top}",
        ))
    return issues


_WARNINGS = (
    hidden_variable, select_all_on_single, directive_names_missing_code,
    conditional_wording_dropped, note_as_answer, renamed_duplicate, odd_qid,
    terminating_answer_not_exclusive, reading_contradicts_document,
)


def consistency_check(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Findings that raise QA_ISSUE."""
    return [i for check in _WARNINGS for i in check(q, by_qid)]


def consistency_notes(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Informational only: never a flag, never blocks."""
    return numeric_without_range(q, by_qid)


def apply(survey, entries: list[dict]) -> None:
    """Run every check over EVERY question (hidden variables and unread questions
    included - the QA loop skips both) and report through the QA record.

    Warnings raise QA_ISSUE and add to `validation.qa_issues`; notes are kept on
    `validation.consistency_notes` and in `entries` with status "note", and never
    flag. Safe to re-run: a question QA does not test has its old output cleared.
    """
    from dataclasses import asdict

    from src.engines.questionnaire.qa_loop import FLAG_QA_ISSUE, _tested

    by_qid = {q.qid: q for q in survey.questions}
    for q in survey.questions:
        if not _tested(q):
            q.flags = [f for f in q.flags if f != FLAG_QA_ISSUE]
            (q.validation or {}).pop("qa_issues", None)
        (q.validation or {}).pop("consistency_notes", None)
        found = consistency_check(q, by_qid)
        notes = consistency_notes(q, by_qid)
        for issue in found:
            entries.append({"qid": q.qid, "agent": "qa", "status": "needs_you", **asdict(issue)})
        for issue in notes:
            entries.append({"qid": q.qid, "agent": "qa", "status": "note", **asdict(issue)})
        if found:
            if FLAG_QA_ISSUE not in q.flags:
                q.flags.append(FLAG_QA_ISSUE)
            lines = [f"{i.detail} ({i.evidence})" if i.evidence else i.detail for i in found]
            q.validation = {
                **(q.validation or {}),
                "qa_issues": list((q.validation or {}).get("qa_issues") or []) + lines,
            }
        if notes:
            q.validation = {**(q.validation or {}), "consistency_notes": [i.detail for i in notes]}
