"""A human sentence for every flag on a question: which question, what was seen.

The review screen used to show a flag as its label plus one description shared
by every question carrying that code. "Ambiguous routing" on SS_EMP said what
the code means in general and nothing about SS_EMP, so a reviewer had to open
the source to find out what was actually wrong. Worse, two flags (an empty
reason, a code the screen had never heard of) rendered as nothing at all.

Reasons are COMPUTED from the question's own state, not stored: a reviewer who
edits the question changes the reason with it, and a question read last month
by an older parser gets the same sentences as one read today. Nothing here
calls a model.

Also owns two facts the reader sees in the source and the definition cannot
hold in a flag alone:

* an entry mode that contradicts the type ("Allow 0-9999999 to be entered" on
  a choice list), and
* an automatic answer ("auto=punch those who select only one at DA_GOALS"),
  which is a derived value, not an unclear base.
"""

from __future__ import annotations

import re
from collections import Counter

from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema

AUTO_ANSWER_KEY = "auto_answer"
ENTRY_CONFLICT_KEY = "entry_mode_conflict"

#: Shown only when nothing on the question explains the flag. It still names the
#: question; the audit counts how often a real document lands here.
GENERIC_SUFFIX = "no detail was recorded with this flag"

_AUTO_ANSWER = re.compile(
    r"auto[\s\-=_]*(?:punch|select|code|assign|answer|fill|check|tick|mark|populate)\w*"
    r"|automatically\s+(?:punch|select|code|assign|record|fill|check|tick|mark)\w*",
    re.I,
)
# A typed-number instruction in the notes: "Allow 0-9999999 to be entered",
# "whole numbers only", "numeric entry".
_NUMERIC_ENTRY = re.compile(
    r"allow\s+-?[\d,]+\s*(?:-|to)\s*-?[\d,]+\s+to\s+be\s+entered"
    r"|whole\s+numbers?\s+only|numeric\s+(?:entry|open)|enter\s+a\s+(?:whole\s+)?number",
    re.I,
)
_CHOICE_TYPES = (
    QuestionType.SINGLE,
    QuestionType.MULTI,
    QuestionType.DROPDOWN,
    QuestionType.RANK,
    QuestionType.GRID_SINGLE,
)


def _clip(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def auto_answer_lines(source_text: str | None) -> list[str]:
    """Each source line that says an answer is filled in by the system."""
    lines = []
    for line in (source_text or "").splitlines():
        match = _AUTO_ANSWER.search(line)
        if match:
            lines.append(_clip(line[match.start() :], 200))
    return lines


def entry_mode_conflict(question: Question) -> str | None:
    """The document's typed-entry instruction, when the type is a choice list.

    A question the reader could not type at all is skipped: its `single` is a
    placeholder, and blaming the document for it would be our mistake.
    """
    if question.type not in _CHOICE_TYPES or not (question.options or question.rows):
        return None
    if "type" in ((question.validation or {}).get("unresolved_fields") or []):
        return None
    # Only the notes the scripter wrote, never an answer label: "Enter a number
    # of employees" can be a legitimate option text.
    for line in (question.source_text or "").splitlines():
        match = _NUMERIC_ENTRY.search(line)
        if match and not re.match(r"\s*\d{1,3}\s*[.):]", line):
            return _clip(line)
    return None


def mark_derived_facts(survey: SurveyDefinition) -> None:
    """Record automatic answers and entry-mode conflicts on each question.

    An automatic answer ("auto=punch those who select only one at DA_GOALS") is
    stored as `validation["auto_answer"]`, and the base flag the reader raised
    for the sentence that merely STATES it is dropped: the sentence is the
    answer's rule, not a base nobody could read. A base that is also stated
    elsewhere on the question keeps its flag.
    """
    for q in survey.questions:
        lines = auto_answer_lines(q.source_text)
        if lines:
            validation = dict(q.validation or {})
            validation[AUTO_ANSWER_KEY] = lines
            verbatim = str(validation.get("routing_verbatim") or "")
            if (
                verbatim
                and _AUTO_ANSWER.search(verbatim)
                and q.show_if is None
                and schema.FLAG_AMBIGUOUS_ROUTING in q.flags
                and "terminate_gap" not in q.extraction_evidence
            ):
                validation.pop("routing_verbatim")
                q.flags = [f for f in q.flags if f != schema.FLAG_AMBIGUOUS_ROUTING]
            q.validation = validation
            q.extraction_evidence.setdefault("auto_answer", lines[0])
        conflict = entry_mode_conflict(q)
        if conflict:
            q.validation = {**(q.validation or {}), ENTRY_CONFLICT_KEY: conflict}
            if schema.FLAG_ENTRY_MODE_CONFLICT not in q.flags:
                q.flags.append(schema.FLAG_ENTRY_MODE_CONFLICT)


def _routing_sentence(q: Question) -> str:
    v = q.validation or {}
    if "terminate_gap" in q.extraction_evidence:
        return f"{q.qid}: {q.extraction_evidence['terminate_gap']}."
    stated = q.extraction_evidence.get("show_if")
    if stated and "confirm" in stated:
        return f"{q.qid}: {stated}."
    loop = v.get("loop")
    if loop and q.show_if is None:
        return (
            f"{q.qid}: iteration {loop.get('index')} ({loop.get('label')}) of the {loop.get('of')} "
            f'loop. The document says "{_clip(q.extraction_evidence.get("loop") or "")}" '
            "but gives no condition that could be applied to this iteration, so it goes to everyone."  # noqa: E501
        )
    verbatim = v.get("routing_verbatim")
    if verbatim:
        return (
            f'{q.qid}: the document states routing — "{_clip(str(verbatim))}" — '
            "that could not be turned into a condition on an earlier question and answer, "
            "so it is not applied."
        )
    for expr, what in ((q.show_if, "base"), (q.terminate_if, "screen-out")):
        if expr is not None and expr.referenced_qids() == {q.qid}:
            return (
                f"{q.qid}: its {what} refers only to {q.qid} itself, which cannot decide "
                "whether it is asked."
            )
    conf = (q.extraction_confidence or {}).get("routing")
    if conf is not None and conf < 0.6:
        return (
            f"{q.qid}: the reader was only {conf:.0%} sure which earlier answer decides "
            "who is asked this."
        )
    return (
        f"{q.qid}: the reader marked who is asked this as ambiguous but kept no sentence; "
        "read its 'Among who' line in the source."
    )


def _duplicates(q: Question) -> str:
    out = []
    for name, group in (("options", q.options), ("rows", q.rows), ("columns", q.cols)):
        dup = sorted(c for c, n in Counter(o.code for o in group or []).items() if n > 1)
        if dup:
            out.append(f"{name} {dup}")
    return "; ".join(out)


def _error(q: Question, key: str) -> str:
    err = (q.validation or {}).get(key) or {}
    return _clip(str(err.get("error_detail") or ""), 140)


def flag_reason(q: Question, code: str) -> str:
    """One sentence naming the question and what was seen for this flag."""
    v = q.validation or {}
    qid = q.qid
    if code == schema.FLAG_AMBIGUOUS_ROUTING:
        return _routing_sentence(q)
    if code == schema.FLAG_UNRESOLVED_ROUTING_QID:
        gone = v.get("unresolved_routing") or []
        named = "; ".join(f"{g['field']} names {', '.join(g['qids'])}" for g in gone)
        return (
            f"{qid}: "
            + (named or "its routing names a question")
            + " that does not exist anywhere in the document, so that condition was removed."
        )
    if code == schema.FLAG_DUPLICATE_OPTION_CODE:
        return f"{qid}: two answers share a code ({_duplicates(q) or 'see the list'}); each needs its own."  # noqa: E501
    if code == schema.FLAG_EMPTY_OPTIONS:
        if q.type is QuestionType.GRID_SINGLE:
            return (
                f"{qid}: a grid with {len(q.rows or [])} rows and {len(q.cols or [])} "
                "columns — it needs both to produce any data column."
            )
        return f"{qid}: a {q.type.value} question was read with no answer options."
    if code == schema.FLAG_VERIFICATION_DISCREPANCY:
        checks = v.get("verification_checks") or []
        if checks:
            c = checks[0]
            more = f" (+{len(checks) - 1} more)" if len(checks) > 1 else ""
            return (
                f"{qid}: check '{c.get('check')}' failed — {c.get('detail')}"
                + (f": {_clip(str(c.get('evidence') or ''), 120)}" if c.get("evidence") else "")
                + more
                + "."
            )
        found = v.get("verification_discrepancies") or []
        if found:
            return f"{qid}: the second-pass verifier disagreed with the reading — {_clip(str(found[0]))}."  # noqa: E501
        return f"{qid}: the second-pass verifier judged the reading unfaithful to the source; {GENERIC_SUFFIX}."  # noqa: E501
    if code == schema.FLAG_VERIFICATION_UNAVAILABLE:
        why = _error(q, "verification_error")
        return (
            f"{qid}: the second-pass check could not run"
            + (f" ({why})" if why else "")
            + "; confirm it against the source by hand."
        )
    if code in (
        schema.FLAG_EXTRACTION_UNAVAILABLE,
        schema.FLAG_EXTRACTION_CONFIG_ERROR,
        schema.FLAG_EXTRACTION_MODEL_ERROR,
    ):
        why = _error(q, "extraction_error")
        left = v.get("unresolved_fields") or []
        return (
            f"{qid}: could not be fully read"
            + (f" ({why})" if why else "")
            + (f"; still unknown: {', '.join(left)}" if left else "")
            + "; fill it in from the source."
        )
    if code == schema.FLAG_BRIEF_RULE:
        rules = v.get("brief_rules") or []
        return (
            f"{qid}: the study instructions say — {_clip(str(rules[0]))}"
            if rules
            else f"{qid}: a study instruction applies; {GENERIC_SUFFIX}."
        )
    if code == schema.FLAG_DERIVED_VARIABLE:
        why = q.extraction_evidence.get("derived") or "its name or wording"
        return f"{qid}: computed by the programmer, not asked (read from {_clip(str(why), 80)}); its derivation is not built."  # noqa: E501
    if code == schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED:
        return f'{qid}: read as a hidden variable from its wording alone ("{_clip(q.text, 70)}"); confirm it is not a real question.'  # noqa: E501
    if code == schema.FLAG_LIST_INHERITED:
        return f"{qid}: {q.extraction_evidence.get('options') or 'its answer list was copied from an earlier question'}; confirm it is the right list."  # noqa: E501
    if code == schema.FLAG_TRADE_OFF_UNSUPPORTED:
        return f"{qid}: {q.extraction_evidence.get('trade_off') or 'a MaxDiff or conjoint exercise'} — an experimental design the platform cannot generate."  # noqa: E501
    if code == "QA_ISSUE":
        issues = v.get("qa_issues") or []
        return (
            f"{qid}: {_clip(str(issues[0]), 220)}"
            + (f" (+{len(issues) - 1} more)" if len(issues) > 1 else "")
            if issues
            else f"{qid}: the QA agent found a problem; {GENERIC_SUFFIX}."
        )
    if code == "RECOVERED_FROM_DATAMAP":
        return f"{qid}: not found in the document; rebuilt from the data map, so its wording and routing are the script's, not the client's."  # noqa: E501
    if code == schema.FLAG_ENTRY_MODE_CONFLICT:
        return entry_conflict_sentence(q)
    if code == schema.FLAG_TYPE_NOT_SUPPORTED:
        kind = str(v.get("intended_type") or "another kind").replace("_", " ")
        return (f"{qid}: this is a {kind} question, which the link cannot run yet; it is "
                f"set up as a {q.type.value.replace('_', ' ')} for now — script it by hand "
                "or approve the stand-in.")
    return f"{qid}: flagged as {code.replace('_', ' ').lower()}; {GENERIC_SUFFIX}."


def entry_conflict_sentence(q: Question) -> str:
    seen = (q.validation or {}).get(ENTRY_CONFLICT_KEY) or entry_mode_conflict(q) or ""
    return (
        f'{q.qid}: the document says "{_clip(seen, 100)}" (a typed number) but the question '
        f"was read as a {q.type.value} choice list of {len(q.options or [])} answers; "
        "decide whether respondents type a number or pick a band."
    )


def flag_reasons(q: Question) -> dict[str, str]:
    """Every flag on `q` mapped to its reason sentence."""
    return {code: flag_reason(q, code) for code in q.flags or []}


def is_generic(reason: str) -> bool:
    return GENERIC_SUFFIX in reason or "kept no sentence" in reason
