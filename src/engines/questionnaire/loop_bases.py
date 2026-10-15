"""Who sees each turn of a loop, from the definitions the document lists.

A looped question does not have one base — it has one per iteration, and the
document spells them out:

    Among who: Provider Current or Lapsed User based on DA_PLATFORM_USAGE
    [Google Ads current user (DA_PLATFORM_USAGE 1,2,3, or 4=5),
     Google Ads Lapsed User (... DA_PLATFORM_USAGE 1, 2, 3, or 4 = 4),
     LinkedIn Current Users (DA_PLATFORM_USAGE 10 = 5),
     LinkedIn Lapsed Users (DA_PLATFORM_USAGE 10 = 4), ...]

Each entry names a brand, a user state, and an exact condition on rows of an
earlier grid: "DA_PLATFORM_USAGE 10 = 5" is row 10 answered 5, and "1,2,3, or
4=5" is any of rows 1 to 4 answered 5, because one brand can span several rows.

Matching them to the loop's iterations is the whole job. An iteration whose
brand appears in no definition gets NO base and keeps its flag: asking every
brand of everybody is the failure this exists to prevent, and a guess here is
indistinguishable from a correct answer in the data.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression

# One definition: "<label> (<QID> <rows> = <value>)". The condition is inside
# the brackets; the words before it name the brand and the state.
_DEFINITION_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 .&'/-]{1,60}?)\s*"
    r"\(\s*(?:NOT\s+[^()]*?AND\s+)?"
    r"(?P<qid>[A-Za-z][A-Za-z0-9_]{2,40})\s*"
    # "1,2,3, or 4" — a comma AND a conjunction between the last two, which is
    # how anybody writes a list. Requiring one separator matched the single-row
    # brands and silently skipped every multi-row one, so the two biggest
    # brands in the study got no base at all.
    r"(?P<rows>\d+(?:\s*,?\s*(?:or|and)?\s*\d+)*)\s*"
    r"=\s*(?P<value>\d+)\s*\)",
    re.I,
)
_ROW_RE = re.compile(r"\d+")


def parse_iteration_bases(text: str) -> dict[str, Expression]:
    """Label → the condition that admits it, for every definition stated.

    Labels are lowercased and stripped of the state words ("current user",
    "lapsed users") so "Google Ads current user" and "Google Ads Lapsed User"
    both answer to "google ads" — a brand is shown its questions if it is in
    ANY of the states listed for it, which is what "Current or Lapsed User"
    says.
    """
    by_label: dict[str, list[Expression]] = {}
    for match in _DEFINITION_RE.finditer(text or ""):
        label = _brand(match.group("label"))
        if not label:
            continue
        rows = [int(r) for r in _ROW_RE.findall(match.group("rows"))]
        value = int(match.group("value"))
        qid = match.group("qid")
        # "NOT Google Ads Current User AND ..." qualifies a LAPSED state. The
        # negated half is another brand's state, not this one's, and reading it
        # here would exclude people the document includes — so only the exact
        # half is taken, and the two states are OR-ed together below anyway.
        alternatives = [
            Expression(
                op="in", qid=qid, row=row, values=[value],
                verbatim_text=match.group(0).strip(),
            )
            for row in rows
        ]
        condition = (
            alternatives[0]
            if len(alternatives) == 1
            else Expression(op="or", children=alternatives,
                            verbatim_text=match.group(0).strip())
        )
        by_label.setdefault(label, []).append(condition)

    return {
        label: (
            conditions[0]
            if len(conditions) == 1
            else Expression(op="or", children=conditions,
                            verbatim_text="; ".join(
                                c.verbatim_text or "" for c in conditions))
        )
        for label, conditions in by_label.items()
    }


_STATE_WORDS = re.compile(
    r"\b(current|lapsed|non|past|former|users?|user|aware|awareness)\b", re.I
)
# "OR  TikTok Lapsed Users" — the conjunction that joined this entry to the
# previous one is not part of the brand's name, and left on it produced a brand
# nobody would ever match against.
_LEADING_CONJUNCTION = re.compile(r"^\s*(?:or|and)\b\s*", re.I)


def _brand(label: str) -> str:
    """The brand a definition is about, with the state words removed."""
    without_state = _STATE_WORDS.sub(" ", _LEADING_CONJUNCTION.sub("", label or ""))
    return " ".join(without_state.split()).strip(" .,-").lower()


def base_for(label: str, bases: dict[str, Expression]) -> Expression | None:
    """The condition for one iteration, or None when the document names none.

    Matched on the brand name alone. None is a real answer — the caller leaves
    the iteration unrouted and flagged, rather than admitting everybody.
    """
    wanted = _brand(label)
    if wanted in bases:
        return bases[wanted]
    # "LinkedIn" against a definition written as "LinkedIn Ads", or the reverse.
    for known, condition in bases.items():
        if known and (known in wanted or wanted in known):
            return condition
    return None


# --- Bases that point at a hidden variable -----------------------------------

_STATE = re.compile(r"\b(current|lapsed|aware|non)\b", re.I)
_QID_NAMED = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")


def _words(text: str) -> set[str]:
    return {w.lower() for w in _STATE.findall(text or "")}


def _hidden_answers(survey) -> list[tuple[str, int, str]]:
    """(qid, code, label) for every value of every hidden variable."""
    out = []
    for q in survey.questions:
        hidden = "DERIVED_VARIABLE" in (q.flags or []) or q.qid.upper().startswith("HID")
        if hidden:
            out += [(q.qid, o.code, o.label or "") for o in q.options or []]
    return out


def resolve_loop_bases_from_hidden(survey) -> list[str]:
    """Each loop turn's base, read from the hidden variable its base points at.

        Among who: Provider aware based on HID_AWARE      (loop: Google Ads, Meta Ads, ...)
        HID_AWARE: 1 Google Ads Aware / 2 Meta Ads Aware / ...

    The Google Ads turn is asked of HID_AWARE = 1. "Platform current user" with
    no variable named is matched the same way across every hidden variable.
    A turn is resolved only when exactly ONE value names its brand and every
    state word of the base (current / lapsed / aware / non); anything else
    stays flagged with its words — two plausible answers is not an answer.
    """
    from src.engines.questionnaire import schema

    answers = _hidden_answers(survey)
    resolved = []
    for q in survey.questions:
        loop = (q.validation or {}).get("loop") or {}
        brand = _brand(loop.get("label") or "")
        if not brand or q.show_if is not None:
            continue
        base = re.sub(r"^\s*Among who:\s*", "", (q.extraction_evidence or {}).get("base", ""))
        if not base or schema.FLAG_AMBIGUOUS_ROUTING not in (q.flags or []):
            continue
        from src.engines.questionnaire.spec_table import base_condition

        stated = base_condition(base)
        if stated is not None and brand in base.lower():
            # "Google Ads Lapsed or Aware Non-User (HID_GOOGLE_USER = 2 or 3)" is
            # written for the Google turn; the other turns are not and stay flagged.
            hits = [(stated.qid, None, base)]
        else:
            hits = None
        named = set(_QID_NAMED.findall(base))
        pool = [a for a in answers if not named or a[0] in named]
        state = _words(base)
        if hits is None:
            hits = [a for a in pool
                    if brand in a[2].lower() and state and state <= _words(a[2])
                    and _words(a[2]) <= state]
        if len(hits) != 1:
            continue
        qid, code, label = hits[0]
        q.show_if = (Expression(op="in", qid=qid, values=stated.values, verbatim_text=base)
                     if code is None else
                     Expression(op="in", qid=qid, values=[code], verbatim_text=base))
        q.extraction_evidence["show_if"] = f"{base} → {qid} = {code} ({label})"
        q.flags = [f for f in q.flags if f != schema.FLAG_AMBIGUOUS_ROUTING]
        if (q.validation or {}).get("routing_verbatim") == base:
            q.validation.pop("routing_verbatim")
        resolved.append(q.qid)
    return resolved


def resolve_answer_bases_from_hidden(survey) -> list[str]:
    """ "Google Ads [IF GOOGLE ADS CURRENT USER]" — one answer's base, naming a
    group a hidden variable defines ("Google Ads Current User"). Resolved when
    exactly one hidden value carries that brand and that state; anything left
    goes to `row_conditions`, which QA reports as shown to everyone."""
    answers = _hidden_answers(survey)
    resolved = []
    for q in survey.questions:
        pending = dict((q.validation or {}).get("answer_conditions") or {})
        if not pending:
            continue
        for option in [*(q.options or []), *(q.rows or [])]:
            words = pending.get(option.label)
            if words is None or option.show_if is not None:
                continue
            state = _words(words)
            hits = [a for a in answers
                    if state and _words(a[2]) == state
                    and (brand := _brand(a[2])) and brand in words.lower()]
            if len(hits) == 1:
                qid, code, label = hits[0]
                option.show_if = Expression(op="in", qid=qid, values=[code], verbatim_text=words)
                resolved.append(f"{q.qid}:{option.code}")
                # A number box cannot be hidden on its own in the link (SurveyJS
                # multipletext items take no condition): keep it in front of QA
                # as "shown to everyone" rather than claim it is applied.
                if not (q.type.value == "numeric" and q.rows):
                    pending.pop(option.label)
        validation = dict(q.validation or {})
        validation.pop("answer_conditions", None)
        if pending:
            validation["row_conditions"] = {**(validation.get("row_conditions") or {}), **pending}
        q.validation = validation
    return resolved
