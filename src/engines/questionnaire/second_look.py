"""A second look at every flagged question, with the whole questionnaire in mind.

The first pass reads one question at a time. When it is unsure it flags, and a
reviewer met the flags as codes: DA_VID_EFFIC carried "Empty options",
"Verification discrepancy" and "QA found a problem" — three alarms for one
cause, none saying how to fix it, while the document held the answer two
questions up. This pass runs after everything else, when every question has
been read, and does what a senior scripter does with a flagged question:

1. **Re-check.** Flags recorded early can be stale: a grid "with no rows" was
   given its rows by a later pass and still showed the alarm. Each question is
   checked again as it stands now; what no longer fails is cleared.
2. **Solve by rule.** Causes the document itself settles — rows inserted from
   another question, a group-name base a hidden variable defines, a name used
   twice, "indicate all" on a one-answer question — are solved from the
   document, the same way every time.
3. **Ask the model, with context.** For the rest, a model sees the question,
   its source, what is wrong, the questions it names, its neighbours, and a
   question of the same kind this document wrote that was read cleanly. Its
   answer may only use wording the document contains, and is kept only when
   the same checks that raised the flag now find less wrong.
4. **Say it once, in plain words.** Each question gets one resolution — fixed
   (with the version before, to undo), a fix ready to apply, a note, or what a
   person must decide with the recommended next step — instead of flag codes.

A question a person approved is never changed. Nothing here is auto-approved:
a fixed question still waits for a reviewer, just without an alarm on it.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from src.core.survey_definition import (
    GRID_TYPES,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.qa_loop import FLAG_QA_ISSUE, Issue, _tested, qa_check
from src.engines.questionnaire.verification import failed, verify_against_source

RESOLUTION_KEY = "resolution"

_UNREAD_FLAGS = frozenset({schema.FLAG_EXTRACTION_UNAVAILABLE, schema.FLAG_EXTRACTION_MODEL_ERROR})

# Flags that mean "a person should look". Informational flags (a hidden
# variable, a list inherited from another question) are not problems.
ATTENTION_FLAGS = frozenset({
    FLAG_QA_ISSUE,
    schema.FLAG_VERIFICATION_DISCREPANCY,
    schema.FLAG_AMBIGUOUS_ROUTING,
    schema.FLAG_UNRESOLVED_ROUTING_QID,
    schema.FLAG_EMPTY_OPTIONS,
    schema.FLAG_DUPLICATE_OPTION_CODE,
    schema.FLAG_ENTRY_MODE_CONFLICT,
    schema.FLAG_TYPE_NOT_SUPPORTED,
    # Not read at all: the model was unreachable when it was first read.
    *_UNREAD_FLAGS,
})

# "[INSERT SELECTIONS FROM DA_VID_ MEASURE]", "SHOW BRANDS SELECTED AT Q2b",
# "ONLY SHOW THOSE AWARE OF AT Q3": the rows are another question's answers.
_ROWS_FROM = re.compile(
    r"\b(?:INSERT|SHOW|DISPLAY|PIPE(?:\s+IN)?)\b[^\]\n]{0,60}?\b(?:FROM|AT)\s+"
    r"(?P<name>[A-Za-z][A-Za-z0-9_]*(?:\s{1,3}[A-Za-z0-9_]+)?)",
    re.I,
)
# A response list written as a scale: "10: Extremely effective", "9: (blank)".
_BLANK = re.compile(r"^\s*\(?\s*blank\s*\)?\s*$|^\s*$", re.I)
# A question name: "Q2b", "S6", "DA_VID_MEASURE".
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*|[A-Z][A-Z0-9]*_[A-Z0-9_]+")
# Answers that are never rows to rate. "Other" can be one; these cannot.
_OPT_OUT = re.compile(
    r"^\s*(?:none\b|nothing\b|don.?t\s+know|do\s+not\s+know|not\s+sure|prefer\s+not|"
    r"refuse|no\s+answer)", re.I)
_SELECT_ALL = re.compile(
    r"\b(?:select|check|mark|choose|tick|indicate)\s+all\b|\ball\s+that\s+apply\b", re.I
)
_SPEC_FIELD = re.compile(
    r"^\s*(?:Among\s+who|Show\s*/\s*HV|Q\s*type|Randomi[sz]e|Other\s+details|Variable\s+list)\b",
    re.I,
)
_SCALE_LINE = re.compile(r"^\s*(\d{1,3})\s*[:.)=]\s*(.*)$")
_AMONG = re.compile(r"Among\s+who\s*:.*?specify\s*:\s*(?P<base>.+)$", re.I | re.M)
_TYPED_RANGE = re.compile(r"allow\s+(-?[\d,]+)\s*(?:-|to)\s*(-?[\d,]+)", re.I)
_NOTE_ONLY = re.compile(r"^\s*\[\s*(?:PN|PROGRAMMER(?:\s+NOTE)?|SCRIPTER)\s*:[^\]]*\]\s*$", re.I)


@dataclass
class Resolution:
    """What the second look concluded about one question."""

    status: str  # "fixed" | "ready" | "explained" | "needs_you"
    summary: str  # one plain sentence a research executive understands
    detail: str = ""  # how it was worked out, or the recommended next step
    source: str = "rule"  # "rule" | "ai:<backend>/<model>"
    after: Question | None = None  # the fixed question (fixed / ready)
    issues: list[str] = field(default_factory=list)  # what was wrong, plainly

    def to_dict(self, before: Question | None = None) -> dict:
        out = {"status": self.status, "summary": self.summary, "detail": self.detail,
               "source": self.source, "issues": self.issues}
        if self.status == "ready" and self.after is not None:
            out["proposed"] = self.after.to_dict()
        if self.status == "fixed" and before is not None:
            out["before"] = before.to_dict()  # one click puts it back
        return out


# --- what is wrong, now ---------------------------------------------------------


def _problems(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Everything wrong with the question as it stands, one list, no duplicates."""
    from src.engines.questionnaire.consistency import consistency_check

    found = list(qa_check(q, by_qid)) if _tested(q) else []
    found += consistency_check(q, by_qid)
    seen: set[tuple[str, str]] = set()
    out = []
    for issue in found:
        key = (issue.check, issue.detail)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def _plain(issue: Issue) -> str:
    return f"{issue.detail} ({issue.evidence})" if issue.evidence else issue.detail


def refresh_flags(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Re-check one question as it stands; clear what no longer fails.

    Returns its current problems. A verification failure recorded while the
    question was first read is replaced by today's result, so "a grid with no
    rows" disappears once a later pass gave it rows.
    """
    v = q.validation if q.validation is not None else {}
    if schema.FLAG_VERIFICATION_DISCREPANCY in q.flags:
        still = failed(verify_against_source(q, q.source_text or ""))
        if still:
            v["verification_checks"] = [
                {"check": c.name, "detail": c.detail, "evidence": c.evidence} for c in still
            ]
        else:
            q.flags = [f for f in q.flags if f != schema.FLAG_VERIFICATION_DISCREPANCY]
            v.pop("verification_checks", None)
    found = _problems(q, by_qid)
    if found:
        if FLAG_QA_ISSUE not in q.flags:
            q.flags.append(FLAG_QA_ISSUE)
        v["qa_issues"] = [_plain(i) for i in found]
    else:
        q.flags = [f for f in q.flags if f != FLAG_QA_ISSUE]
        v.pop("qa_issues", None)
    if schema.FLAG_EMPTY_OPTIONS in q.flags and (q.options or (q.rows and q.cols)):
        q.flags = [f for f in q.flags if f != schema.FLAG_EMPTY_OPTIONS]
    q.validation = v or None
    return found


def _needs_look(q: Question) -> bool:
    return bool(set(q.flags or ()) & ATTENTION_FLAGS)


# --- rows inserted from another question -----------------------------------------


def _find_qid(name: str, by_upper: dict[str, str]) -> str | None:
    """A question name as the document wrote it, broken lines and case included."""
    for candidate in (name, re.sub(r"\s+", "", name), name.split()[0] if name.split() else ""):
        hit = by_upper.get(candidate.upper())
        if hit:
            return hit
    return None


def _scale_from_options(q: Question) -> list[Option] | None:
    """A response list that is a scale: numbered points, most of them unlabelled."""
    opts = q.options or []
    if len(opts) < 3:
        return None
    blank = sum(1 for o in opts
                if _BLANK.match(o.label or "") or (o.label or "").strip() == str(o.code))
    if blank < len(opts) / 2:
        return None
    return [Option(code=o.code, label=str(o.code) if _BLANK.match(o.label or "") else o.label)
            for o in opts]


def _scale_from_source(q: Question) -> list[Option] | None:
    """ "Response list: / 10: Extremely effective / 9: (blank) / …" in a spec table."""
    lines = (q.source_text or "").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^\s*Response\s+list\s*:", line, re.I)), None)
    if start is None:
        return None
    points = []
    for line in lines[start + 1:]:
        if _SPEC_FIELD.match(line):
            break
        match = _SCALE_LINE.match(line)
        if not match:
            continue
        code, label = int(match.group(1)), match.group(2).strip()
        points.append(Option(code=code, label=str(code) if _BLANK.match(label) else label))
    blank = sum(1 for o in points if o.label == str(o.code))
    return points if len(points) >= 3 and blank >= len(points) / 2 else None


def _asked_text(q: Question) -> str | None:
    """The question a respondent reads, in a spec table: the line after "Q Name:"."""
    lines = [line.strip() for line in (q.source_text or "").splitlines() if line.strip()]
    for i, line in enumerate(lines[:-1]):
        if re.match(r"^Q\s*Name\s*:", line, re.I):
            return lines[i + 1]
    return None


def _base_from_source(q: Question, source_qid: str) -> Expression | None:
    """The spec table's "Among who … specify:" base, for a question read here.

    "DA_PLACEMENT = 3 and/or 4 AND 1-98 selected at DA_VID_MEASURE": the part on
    the rows' source is what the mask already enforces; the rest must parse
    exactly, or nothing is applied and the question stays with a person.
    """
    from src.engines.questionnaire.routing_prose import parse_routing

    found = _AMONG.search(q.source_text or "")
    if not found:
        return None
    parts = re.split(r"\s+AND\s+", found.group("base").strip(), flags=re.I)
    kept = [p for p in parts if source_qid.upper() not in re.sub(r"\s+", "", p).upper()]
    if not kept or (len(kept) == len(parts) and len(parts) > 1):
        return None
    parsed = [parse_routing(f"IF {p}") for p in kept]
    if any(e is None for e in parsed):
        return None
    if len(parsed) == 1:
        return parsed[0]
    return Expression(op="and", children=parsed, verbatim_text=found.group("base").strip())


def _rows_source(q: Question, survey: SurveyDefinition, by_qid: dict) -> tuple[str, str] | None:
    """Which question's answers become this question's rows, and how we know."""
    by_upper = {qid.upper(): qid for qid in by_qid}
    techniques = (q.validation or {}).get("techniques") or {}
    for mask in techniques.get("masks") or []:
        source = mask.get("source_qid")
        if source in by_qid and source != q.qid and by_qid[source].options:
            return source, f'the document says "{mask.get("verbatim") or source}"'
    for match in _ROWS_FROM.finditer(q.source_text or ""):
        named = _find_qid(match.group("name"), by_upper)
        said = " ".join(match.group(0).split())
        if named and named != q.qid and by_qid[named].options:
            return named, f'the document says "{said}"'
        if named == q.qid:
            # The document names this question as its own source — a copy slip.
            # The list it meant is the nearest question before it that has one.
            order = survey.question_order()
            here = order.index(q.qid) if q.qid in order else -1
            for prior in reversed(order[:here]):
                other = by_qid.get(prior)
                if other is None or other.type not in (QuestionType.MULTI, QuestionType.SINGLE):
                    continue
                if other.options:
                    return prior, (
                        f'the document says "{said}", naming this question itself — a copy '
                        f"slip; {prior}, just before it, holds the list"
                    )
    return None


def solve_rows_from_another_question(q: Question, survey: SurveyDefinition,
                                     by_qid: dict) -> Resolution | None:
    """A rating grid whose rows are another question's answers ("INSERT SELECTIONS FROM X")."""
    if q.rows:
        return None
    is_grid = q.type in GRID_TYPES
    scale = q.cols or _scale_from_source(q) or (_scale_from_options(q) if not is_grid else None)
    if not scale:
        return None
    found = _rows_source(q, survey, by_qid)
    if found is None:
        return None
    source_qid, how = found
    source = by_qid[source_qid]
    rows = [Option(code=o.code, label=o.label) for o in source.options
            if not o.exclusive and not _OPT_OUT.match(o.label or "")]
    after = copy.deepcopy(q)
    if set(after.flags) & _UNREAD_FLAGS:
        # Read here, by rule, from the source: no longer an unread question.
        after.flags = [f for f in after.flags if f not in _UNREAD_FLAGS]
        after.text = _asked_text(after) or after.text
        if after.show_if is None:
            after.show_if = _base_from_source(after, source_qid)
        after.extraction_evidence = {**(after.extraction_evidence or {}),
                                     "read_by": "second look (spec table rows and scale)"}
    after.type = QuestionType.GRID_SINGLE
    after.rows = rows
    after.cols = scale
    after.options = []
    techniques = dict((after.validation or {}).get("techniques") or {})
    masks = [m for m in techniques.get("masks") or [] if m.get("source_qid") != source_qid]
    masks.append({"source_qid": source_qid, "mode": "selected", "exclude_qid": None,
                  "verbatim": how})
    after.validation = {**(after.validation or {}),
                        "techniques": {"pipes": [], "least_fill": False, **techniques,
                                       "masks": masks}}
    return Resolution(
        status="ready" if "copy slip" in how else "fixed",
        summary=(f"Each answer chosen at {source_qid} becomes a row, rated on the "
                 f"{len(scale)}-point scale."),
        detail=f"{how}. Rows: {len(rows)} from {source_qid}; scale: "
               f"{scale[0].label} … {scale[-1].label}.",
        after=after,
    )


# --- bases, names and answer lists ------------------------------------------------


def solve_group_base(q: Question, survey: SurveyDefinition) -> Resolution | None:
    """ "Google Ads current user", "Meta Ads aware based on HID_AWARE": a group a
    hidden variable defines. Each group is a brand and a state; each hidden value
    spells out one such pair, so the base is the values that match exactly."""
    from src.engines.questionnaire.loop_bases import _QID_NAMED, _brand, _hidden_answers, _words

    loop = (q.validation or {}).get("loop") or {}
    applied = (q.show_if.verbatim_text or "").lower() if q.show_if is not None else ""
    if (applied and loop.get("label") and loop["label"].lower() in applied
            and schema.FLAG_AMBIGUOUS_ROUTING in q.flags):
        # The loop's own definitions already gave this pass its base; only the
        # alarm from the unparsed summary line was left behind.
        after = copy.deepcopy(q)
        after.flags = [f for f in after.flags if f != schema.FLAG_AMBIGUOUS_ROUTING]
        return Resolution(
            status="fixed",
            summary=f"Asked of the {loop['label']} group the document defines for this pass.",
            detail=f'Applied from the document\'s definitions: "{q.show_if.verbatim_text[:160]}".',
            after=after,
        )
    if q.show_if is not None:
        return None
    words = str((q.validation or {}).get("routing_verbatim") or "").split("[")[0].strip()
    states = _words(words)
    if not states:
        return None
    if loop.get("label") and re.search(r"\b(?:provider|brand|platform)\b", words, re.I):
        brand = loop["label"].lower()  # "Provider …" in a loop: this pass's brand
    else:
        brand = _brand(re.split(r"\bbased\s+on\b", words, flags=re.I)[0])
    if not brand:
        return None
    answers = _hidden_answers(survey)
    hidden = {qid for qid, _, _ in answers}
    named = set(_QID_NAMED.findall(words)) & hidden
    hits = [(qid, code, label) for qid, code, label in answers
            if (not named or qid in named)
            and (own := _words(label)) and own <= states
            and (b := _brand(label)) and (b == brand or b in brand or brand in b)]
    if len({qid for qid, _, _ in hits}) != 1:
        return None
    qid = hits[0][0]
    codes = sorted(code for _, code, _ in hits)
    after = copy.deepcopy(q)
    after.show_if = Expression(op="in", qid=qid, values=codes, verbatim_text=words)
    after.flags = [f for f in after.flags if f != schema.FLAG_AMBIGUOUS_ROUTING]
    groups = ", ".join(f'"{label}"' for _, _, label in hits)
    return Resolution(
        status="fixed",
        summary=f"Asked only of {groups} ({qid} = {', '.join(map(str, codes))}).",
        detail=f'The document says "{words}"; {qid} defines exactly that group.',
        after=after,
    )


def solve_typed_number(q: Question) -> Resolution | None:
    """The document says to type a number, but the question was read as a list."""
    from src.engines.questionnaire.flag_reasons import entry_mode_conflict

    said = entry_mode_conflict(q)
    if not said:
        return None
    after = copy.deepcopy(q)
    after.type = QuestionType.NUMERIC
    after.options = []
    span = _TYPED_RANGE.search(said)
    limits = ""
    if span:
        lo, hi = (int(x.replace(",", "")) for x in span.groups())
        after.validation = {**(after.validation or {}), "min": lo, "max": hi}
        limits = f" ({lo:,}–{hi:,})"
    after.flags = [f for f in after.flags if f != schema.FLAG_ENTRY_MODE_CONFLICT]
    return Resolution(
        status="ready",
        summary=f"The document asks for a typed number, so make this a number box{limits}.",
        detail=f'The document says "{said}". The listed answers may be how the number is '
               "grouped later — check before applying.",
        after=after,
    )


def solve_renamed_duplicate(q: Question, issues: list[Issue]) -> Resolution | None:
    """The document used one name twice; the second was renamed. Nothing to do."""
    renamed = [i for i in issues if "QID_RENAMED" in (i.check or "")
               or "this later one was renamed" in (i.detail or "")]
    if not renamed or len(issues) != len(renamed):
        return None
    original = re.sub(r"_\d+$", "", q.qid)
    return Resolution(
        status="explained",
        summary=(f"The document uses the name {original} twice, so this one is saved as "
                 f"{q.qid} and gets its own data column."),
        detail="Nothing to change unless this question was meant to replace the first one.",
    )


def solve_select_all_wording(q: Question, issues: list[Issue]) -> Resolution | None:
    """One answer allowed, but the wording asks for all that apply."""
    if q.type is not QuestionType.SINGLE or not q.options:
        return None
    if not any(i.check == "type_matches_the_wording" or "should it allow several" in i.detail
               for i in issues):
        return None
    said = _SELECT_ALL.search(f"{q.text or ''} {q.source_text or ''}")
    if not said:
        return None
    after = copy.deepcopy(q)
    after.type = QuestionType.MULTI
    return Resolution(
        status="ready",
        summary="The wording asks for every answer that applies, so allow several answers.",
        detail=f'The document says "{said.group(0)}" but the question allowed one answer.',
        after=after,
    )


def solve_exclusive_screen_out(q: Question, issues: list[Issue]) -> Resolution | None:
    """ "None of these [TERMINATE]" in a select-all list must be the only answer."""
    if q.type is not QuestionType.MULTI or q.terminate_if is None:
        return None
    if not any("are not exclusive" in i.detail for i in issues):
        return None
    codes = {c for c in (q.terminate_if.values or []) if isinstance(c, int)}
    targets = [o for o in q.options or [] if o.code in codes and not o.exclusive]
    if not targets:
        return None
    after = copy.deepcopy(q)
    for option in after.options:
        if option.code in codes:
            option.exclusive = True
    names = "; ".join(f'"{o.label}"' for o in targets)
    return Resolution(
        status="ready",
        summary=f"Make {names} the only answer a respondent can pick with it.",
        detail="These answers end the survey. In a select-all list a respondent could tick "
               "them with other answers and the screen-out would be unclear.",
        after=after,
    )


def solve_note_as_answer(q: Question) -> Resolution | None:
    """ "[PN: RANDOMIZE]" read as an answer row: drop it, and do what it says."""
    lists = [name for name in ("options", "rows") if any(
        _NOTE_ONLY.match(o.label or "") for o in getattr(q, name) or [])]
    if not lists:
        return None
    after = copy.deepcopy(q)
    notes = []
    for name in lists:
        kept = []
        for item in getattr(after, name):
            if _NOTE_ONLY.match(item.label or ""):
                notes.append(item.label.strip())
            else:
                kept.append(item)
        setattr(after, name, kept)
    validation = dict(after.validation or {})
    if any(re.search(r"RANDOMI[SZ]E", n, re.I) for n in notes):
        validation["randomize"] = True
    validation["lifted_notes"] = [*(validation.get("lifted_notes") or []), *notes]
    after.validation = validation
    return Resolution(
        status="fixed",
        summary=f"Removed a programmer note that was read as an answer ({notes[0]}).",
        detail="It is kept in the record" + ("; the list is now randomised as it says."
                                             if validation.get("randomize") else "."),
        after=after,
    )


def solve_duplicate_code(q: Question) -> Resolution | None:
    """Two answers with one code: the second gets the next free code."""
    codes = [o.code for o in q.options or []]
    if len(codes) == len(set(codes)):
        return None
    after = copy.deepcopy(q)
    free = max([c for c in codes if c < 90] or [0]) + 1
    moved = []
    taken: set[int] = set()
    for option in after.options:
        if option.code in taken:
            moved.append(f'"{option.label}" {option.code} → {free}')
            option.code = free
            free += 1
        taken.add(option.code)
    after.flags = [f for f in after.flags if f != schema.FLAG_DUPLICATE_OPTION_CODE]
    return Resolution(
        status="ready",
        summary="Two answers share a code; give the second one the next free code.",
        detail="; ".join(moved) + ". Check the data map uses the same codes before fielding.",
        after=after,
    )


_TABLE_TRIPS = ("GRID_ROWS_NOT_STATEMENTS", "ROWS_DIFFER_FROM_DOCUMENT")


def solve_table_rows(q: Question, issues: list[Issue], by_qid: dict) -> Resolution | None:
    """Rows the document states as a table (`Q41_1 | left | right`), read from it.

    Bubble Q41/Q44: pairs of opposite statements read as a grid whose rows were
    a scale label and a column heading. The table names every row, so the rows
    come from the table and the fix is kept only when the tripwires clear.
    """
    from src.engines.questionnaire.bipolar import flips, is_bipolar, read_table

    if not q.cols or not any(i.check in _TABLE_TRIPS for i in issues):
        return None
    after = read_table(q)
    if after is None:
        return None
    remaining = _problems(after, {**by_qid, q.qid: after})
    if any(i.check in _TABLE_TRIPS for i in remaining):
        return None
    kind = "pairs of opposite statements" if is_bipolar(after) else "rows"
    flip = (" Half the respondents see each pair the other way round, as the document "
            "asks; which half is recorded in the data." if flips(after) else "")
    return Resolution(
        status="fixed",
        summary=f"Read the {len(after.rows)} {kind} from the document's table.{flip}",
        detail="Rows before: " + "; ".join((r.label or "")[:40] for r in q.rows or []),
        after=after,
    )


def solve_junk_rows(q: Question, issues: list[Issue], by_qid: dict) -> Resolution | None:
    """Scale lines or a column heading read as grid rows: drop them, keep the rest.

    Vivo Q13 had its three scale lines as rows above the ten pharmacies. Only
    when the scale is already the columns and real rows remain — otherwise the
    rows themselves were lost (Bubble Q41) and dropping leaves nothing.
    """
    from src.engines.questionnaire.consistency import is_junk_row

    if not q.cols or not any(i.check == "GRID_ROWS_NOT_STATEMENTS" for i in issues):
        return None
    keep = [r for r in q.rows or [] if not is_junk_row(r.label, q)]
    if len(keep) < 2:
        return None
    after = copy.deepcopy(q)
    after.rows = keep
    remaining = _problems(after, {**by_qid, q.qid: after})
    if any(i.check in ("GRID_ROWS_NOT_STATEMENTS", "ROWS_DIFFER_FROM_DOCUMENT") for i in remaining):
        return None
    dropped = [r.label for r in q.rows if is_junk_row(r.label, q)]
    return Resolution(
        status="fixed",
        summary=f"Removed {len(dropped)} row{'s' if len(dropped) != 1 else ''} that "
                "were the answer scale, not things to rate.",
        detail="Removed: " + "; ".join(d[:50] for d in dropped),
        after=after,
    )


def _likely_name(missing: str, qids: list[str]) -> str | None:
    """DA_USAGE → DA_PLATFORM_USAGE, C_ZIP → C_GEO_ZIP: the one real name that
    keeps every part of the missing one, in order, or ends the same way."""
    parts = [p for p in missing.upper().split("_") if p]
    if not parts:
        return None

    def keeps(name: str) -> bool:
        it = iter(name.upper().split("_"))
        return all(any(part == t for t in it) for part in parts)

    hits = [q for q in qids if q.upper() != missing.upper() and keeps(q)]
    if not hits and len(parts) > 1:
        tail = "_".join(parts[1:])
        hits = [q for q in qids
                if q.upper().endswith("_" + tail) and not q.upper().startswith("HID")]
    return hits[0] if len(hits) == 1 else None


# What a person should do, by what is wrong — so no problem arrives without a next step.
_ADVICE = (
    (re.compile(r"no question called (\w+)"), None),  # filled in with the likely name
    (re.compile(r"names (\w+) that does not exist"), None),
    (re.compile(r"'\?\?'"), "The document leaves the limit as '??'. Ask the author for "
                            "it; the rest of the screen-out already applies."),
    (re.compile(r'says "Anchor (\d+)" but the list has no code'),
     "The anchor names a code the list does not have — usually the 'Other' or last "
     "answer. Pick the answer to keep last, or ask the author."),
    (re.compile(r"fall in no group"), "Add those answers to a group, or confirm they "
                                       "should get no value."),
    (re.compile(r"scripter's note|scripter directive|programmer note"),
     "Remove the note from the wording in the editor; it is kept in the record."),
    (re.compile(r"only for some respondents; the survey shows it to everyone"),
     "The link cannot hide a single box yet, so everyone sees this one. Accept that "
     "(analysis can drop it for non-users) or split the question."),
    (re.compile(r"second copy of"), "Merge the two, or set who sees each."),
    (re.compile(r"contains '\('"), "Rename it in the editor — a bracket breaks the "
                                   "data column name."),
)


def _advice(lines: list[str], qids: list[str]) -> str:
    for line in lines:
        for pattern, advice in _ADVICE:
            match = pattern.search(line)
            if not match:
                continue
            if advice:
                return advice
            missing = match.group(1)
            likely = _likely_name(missing, qids)
            if likely:
                return (f"{missing} is probably {likely} — the only question whose name "
                        "matches. Correct the name in the document or here.")
            return f"{missing} is not in the document; check the name with the author."
    return ("Nothing in the document settles this on its own — a reviewer decides. "
            "Open the source beside it, or describe the fix to the agent.")


# --- the model, with the whole questionnaire in view -----------------------------


def _mentioned(q: Question, by_qid: dict) -> list[Question]:
    """Questions this one names anywhere: its source, conditions, masks, pipes."""
    by_upper = {qid.upper(): qid for qid in by_qid}
    names = {by_upper[t.upper()] for t in _NAME.findall(q.source_text or "")
             if t.upper() in by_upper}
    if q.show_if is not None:
        names |= q.show_if.referenced_qids()
    techniques = (q.validation or {}).get("techniques") or {}
    for item in [*(techniques.get("masks") or []), *(techniques.get("pipes") or [])]:
        if item.get("source_qid"):
            names.add(item["source_qid"])
    names.discard(q.qid)
    return [by_qid[n] for n in sorted(names) if n in by_qid][:6]


def _exemplar(q: Question, survey: SurveyDefinition) -> Question | None:
    """A question of the same kind this document wrote that was read without trouble."""
    for other in survey.questions:
        if other.qid == q.qid or _needs_look(other) or not other.source_text:
            continue
        if other.type is q.type and (other.options or other.rows):
            return other
    return None


def _context(q: Question, survey: SurveyDefinition, by_qid: dict) -> tuple[str, list[Question]]:
    from src.engines.questionnaire.question_agent import _describe

    order = survey.question_order()
    here = order.index(q.qid) if q.qid in order else -1
    neighbours = [by_qid[x] for x in (order[max(here - 1, 0):here] + order[here + 1:here + 2])
                  if here >= 0 and x in by_qid and x != q.qid]
    mentioned = _mentioned(q, by_qid)
    parts = []
    overview = (survey.metadata or {}).get("study_overview") or {}
    brief = {k: overview[k] for k in ("title", "objectives", "audience", "total_sample")
             if overview.get(k)}
    if brief:
        parts.append(f"About the study: {brief}")
    if mentioned:
        parts.append("Questions this one names:\n" + "\n".join(_describe(o) for o in mentioned))
    if neighbours:
        parts.append("The questions around it:\n" + "\n".join(_describe(o) for o in neighbours))
    example = _exemplar(q, survey)
    if example is not None:
        parts.append(
            "How this document writes a question of this kind, and its correct reading:\n"
            f"SOURCE:\n{(example.source_text or '')[:800]}\nREAD AS:\n{_describe(example)}"
        )
    return "\n\n".join(parts), [*mentioned, *neighbours]


def solve_with_model(q: Question, survey: SurveyDefinition, by_qid: dict,
                     issues: list[Issue]) -> Resolution | None:
    """Ask a model, with the questionnaire around the question, to fix it."""
    from src.engines.questionnaire.question_agent import revise_with_context

    context, related = _context(q, survey, by_qid)
    instruction = (
        "Fix what QA found, using the questionnaire around it:\n- "
        + "\n- ".join(_plain(i) for i in issues)
    )
    suggestion = revise_with_context(q, instruction, by_qid, related, context,
                                     task="questionnaire.resolve")
    if "error" in suggestion:
        return None
    after = Question.from_dict(suggestion["question"])
    remaining = _problems(after, {**by_qid, q.qid: after})
    if len(remaining) >= len(issues) or suggestion.get("dropped"):
        return None  # not better, or it reached for words the document does not have
    return Resolution(
        status="fixed" if not remaining else "ready",
        summary=suggestion["explanation"],
        detail=("Worked out from the question's source and the questions it names."
                + (f" Still open: {'; '.join(_plain(i) for i in remaining)}" if remaining else "")),
        source=f"ai:{suggestion['source'].removeprefix('agent (').rstrip(')')}",
        after=after,
    )


# --- the pass --------------------------------------------------------------------


def _filled_in_since(q: Question, issues: list[Issue]) -> Resolution | None:
    """Unread at first, complete now (a person filled it in), nothing wrong."""
    if issues or not set(q.flags) & _UNREAD_FLAGS or set(q.flags) & (
            ATTENTION_FLAGS - _UNREAD_FLAGS):
        return None
    has_content = (q.rows and q.cols) or q.options or q.type in (
        QuestionType.NUMERIC, QuestionType.OPEN_TEXT, QuestionType.INFO)
    if not has_content:
        return None
    return Resolution(
        status="explained",
        summary="Filled in after the first reading, and nothing is wrong with it now.",
        detail="Check it against the source and approve it.",
    )


def _explain(q: Question, issues: list[Issue], qids: list[str]) -> Resolution:
    from src.engines.questionnaire.flag_reasons import flag_reasons

    lines = [_plain(i) for i in issues] or [
        reason for code, reason in flag_reasons(q).items() if code in ATTENTION_FLAGS and reason
    ]
    first = lines[0] if lines else "the reading of this question is uncertain"
    return Resolution(
        status="needs_you",
        summary=first[0].upper() + first[1:],
        detail=_advice(lines, qids),
        issues=lines,
    )


def second_look(survey: SurveyDefinition, *, use_model: bool = True,
                locked: set[str] | None = None, only: set[str] | None = None) -> dict:
    """Re-check, solve and explain every flagged question. Returns the record.

    `only`: just these questions (after a person saved one), the rest of the
    record kept as it was.
    """
    locked = locked or set()
    entries = []
    for q in survey.questions:
        if only is not None and q.qid not in only:
            continue
        by_qid = {x.qid: x for x in survey.questions}
        issues = refresh_flags(q, by_qid)
        (q.validation or {}).pop(RESOLUTION_KEY, None)
        if not _needs_look(q):
            continue
        before = copy.deepcopy(q)
        resolution = (
            # Only right after a person saved it: an unread question whose
            # leftover text trips no check must not be called "fine".
            (_filled_in_since(q, issues) if only is not None else None)
            or solve_renamed_duplicate(q, issues)
            or solve_group_base(q, survey)
            or solve_typed_number(q)
            or solve_select_all_wording(q, issues)
            or solve_exclusive_screen_out(q, issues)
            or solve_note_as_answer(q)
            or solve_duplicate_code(q)
            or solve_table_rows(q, issues, by_qid)
            or solve_junk_rows(q, issues, by_qid)
            or solve_rows_from_another_question(q, survey, by_qid)
        )
        if resolution is None and use_model and issues and q.qid not in locked:
            resolution = solve_with_model(q, survey, by_qid, issues)
        if resolution is None:
            resolution = _explain(q, issues, list(by_qid))
        resolution.issues = resolution.issues or [_plain(i) for i in issues]
        if resolution.status == "fixed" and q.qid in locked:
            resolution.status = "ready"  # a person approved it: offer, never change
        if resolution.status == "fixed" and resolution.after is not None:
            q.__dict__.update(copy.deepcopy(resolution.after).__dict__)
            refresh_flags(q, {**by_qid, q.qid: q})
        elif resolution.status == "explained":
            q.flags = [f for f in q.flags if f != FLAG_QA_ISSUE]
        q.validation = {**(q.validation or {}), RESOLUTION_KEY: resolution.to_dict(before)}
        entries.append({"qid": q.qid, **{k: v for k, v in resolution.to_dict().items()
                                          if k in ("status", "summary", "source")}})
    if only is not None:
        kept = [e for e in (survey.metadata.get("second_look") or {}).get("entries", [])
                if e["qid"] not in only]
        entries = kept + entries
    record = {
        "checked": len(survey.questions),
        "fixed": sum(e["status"] == "fixed" for e in entries),
        "ready": sum(e["status"] == "ready" for e in entries),
        "explained": sum(e["status"] == "explained" for e in entries),
        "needs_you": sum(e["status"] == "needs_you" for e in entries),
        "entries": entries,
    }
    survey.metadata["second_look"] = record
    return record
