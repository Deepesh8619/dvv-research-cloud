"""Every clause of every note gets an outcome, and nothing is dropped silently.

    note -> clauses -> kind -> outcome
        applied   a setting on the survey changed
        asked     a person must decide ("What a reader would ask")
        scripter  cannot be automated; on the scripter's checklist
        by_design says nothing new ("Ask all")
        unread    nothing understood it — counted, so it can be driven down

The kind comes from the words; the outcome comes from the survey. A clause that
names a kind whose field is still empty is `unread`, never `applied`.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.core.survey_definition import QuestionType, SurveyDefinition
from src.engines.questionnaire.note_lines import note_lines, sentences
from src.engines.questionnaire.reading_questions import analyst_reading

OUTCOMES = ("applied", "asked", "scripter", "by_design", "unread")
CAP = 2000


def _k(kind: str, pattern: str) -> tuple[str, re.Pattern]:
    return kind, re.compile(pattern, re.I)


# Ordered: a clause gets a record for every kind it matches.
KINDS: tuple[tuple[str, re.Pattern], ...] = (
    _k("number_entry", r"\bwhole\s+number|\bnumeric\b|\bnumber\s+only\b|\benter\s+a\s+number|\bdigits?\b"),  # noqa: E501
    _k("range", r"\brange\b|\bbetween\s+\d+\s+and\s+\d+|\b(?:min(?:imum)?|max(?:imum)?)\s+(?:value\s+)?(?:of\s+)?\d"),  # noqa: E501
    _k("terminate", r"\bterminat|\bscreen[- ]?out|\bdisqualif|\bclose\s+out|\bend\s+(?:the\s+)?survey"),  # noqa: E501
    _k("exclusive", r"\bexclusive\b|\bcannot\s+(?:be\s+)?combine|\bdo\s+not\s+allow\s+(?:any\s+other|multi)|\ballow\s+multiple\s+responses?\s+except"),  # noqa: E501
    _k("reask", r"\bsecond\s+time\b|\bask\s+again\b|\bre-?ask\b|\bre-?show\b"),
    _k("hidden_var", r"\bcreate\b.*\bvariables?\b|\bhidden\s+variable|\bdummy\s+variable|\bhold\s+variable|\brecode\b"),  # noqa: E501
    _k("base", r"\b(?:ask|asked|show)\s+(?:module\s+)?(?:only\s+)?(?:if|for|those)\b|^\s*(?:base|filter)\b|\bonly\s+ask\b"),  # noqa: E501
    _k("mask", r"\bmask\b|\bonly\s+show\s+(?:those\s+)?selected|\bshow\s+(?:only\s+)?(?:those\s+|brands?\s+)?(?:not\s+)?selected\s+at|\bhide\s+answers?|\bcarry[- ]?forward"),  # noqa: E501
    _k("randomize", r"\brandomi[sz]|\brotate\b|\bshuffle"),
    _k("anchor", r"^(?!.*\btogether\b).*(?:\banchor|\b(?:keep|lock|fix(?:ed)?)\s+punch|\bat\s+the\s+(?:bottom|top)\b)"),  # noqa: E501
    _k("pipe", r"\bpipe[ds]?\b|\bpiping\b|\binsert\s+(?:the\s+)?(?:response|answer|text)"),
    _k("limit_select", r"\bselect\s+(?:at\s+least|up\s+to|at\s+most|exactly)\b|\b(?:up\s+to|max(?:imum)?\s+of)\s+\d+\s+(?:responses?|selections?|answers?)"),  # noqa: E501
    _k("one_or_many", r"\b(?:allow|accept)\s+(?:only\s+)?(?:one|a\s+single|single|multiple|more\s+than\s+one)\b|\bselect\s+(?:only\s+)?one\b|\bselect\s+all\s+that\s+apply"),  # noqa: E501
    _k("open_end_limit", r"\bcharacter|\bOE\s*:|\bopen[- ]end|\bminimum\s+\d+\s+words?"),
    _k("sum", r"\bsum\b|\btotals?\s+(?:to|must|=|equals?)|\badd\s+up\s+to|\bconstant\s+sum"),
    _k("quota", r"\bquotas?\b"),
    _k("layout", r"\blayout\b|\bcarr?ousel\b|\bintermediate\s+text\s+only\b|\bside[- ]by[- ]side\b|\bper\s+(?:screen|page)\b|\bsame\s+(?:screen|page)\b|\bdrop-?down\b|\bslider\b|\bhorizontal\b|\bvertical\b"),  # noqa: E501
    _k("stimulus", r"\bstimul|\bconcept\b|\bvideo\b|\bimage\b|\blogo\b|\bmonadic\b|\bscreenshot\b"),
    _k("loop", r"\bloop\b|\bfor\s+each\s+(?:brand|item|product|selected)\b|\biterat"),
    _k("quality", r"\bquality\b|\bflag\s+(?:if|any|respondents?)\b|\bspeeder|\bstraight-?line|\battention\s+check|\bauto-?punch"),  # noqa: E501
    _k("warning", r"\bshow\s+(?:a\s+)?warning\b"),
    _k("ask_all", r"^(?!.*\bexcept\b).*(?:\b(?:ask|show)\s+(?:all|everyone|everybody)\b|\bshow\s+to\s+all\b|\bto\s+all\s+respondents\b|\brespondents?\s+can\s+see\b)"),  # noqa: E501
)

# "Prefer not to answer" in the same sentence does not negate the randomising.
_NEGATED = re.compile(
    r"\b(?:do\s+not|don'?t|never)\s+(?:randomi[sz]|rotate|shuffle)|:\s*no\b", re.I)
_TOGETHER = re.compile(r"\btogether\b|\bwithin\s+(?:pair|\d)", re.I)
_NOT_A_NOTE = re.compile(r"^\W*blank\W*$|^end\s+screen\W*$|^also\s+worded\s+as\b", re.I)
_FIXED = {"quota": "scripter", "layout": "scripter", "stimulus": "scripter",
          "quality": "scripter", "ask_all": "by_design"}


def _validation(q) -> dict:
    return q.validation or {}


def _techniques(q) -> dict:
    return _validation(q).get("techniques") or {}


# kind -> (check on the question, what it found). Empty field means unread.
_CHECKS = {
    "number_entry": lambda q, _: q.type is QuestionType.NUMERIC,
    "range": lambda q, _: "min" in _validation(q) or "max" in _validation(q),
    "terminate": lambda q, _: q.terminate_if is not None,
    "exclusive": lambda q, _: any(o.exclusive for o in q.options or []),
    "base": lambda q, _: q.show_if is not None or any(o.show_if for o in q.options or []),
    "mask": lambda q, _: bool(_techniques(q).get("masks")),
    "anchor": lambda q, _: any(o.anchored for o in q.options or []),
    "pipe": lambda q, _: bool(q.piping_refs or _techniques(q).get("pipes")),
    "limit_select": lambda q, _: "min_select" in _validation(q) or "max_select" in _validation(q),
    "one_or_many": lambda q, _: q.type in (QuestionType.SINGLE, QuestionType.MULTI) and bool(q.options),  # noqa: E501
    "open_end_limit": lambda q, _: "min_length" in _validation(q) or "max_length" in _validation(q),  # noqa: E501
    "sum": lambda q, _: any("sum" in k or "total" in k for k in _validation(q)),
}


_MADE_SET = {"base": "show_if", "anchor": "anchored", "exclusive": "exclusive",
             "terminate": "terminate_if", "range": "range"}
_QIDS = re.compile(r"\b[A-Z]{1,4}\d+[a-z]?\b")
_CODES = re.compile(r"\b(?:punch(?:es)?|codes?)\s+((?:\d+\s*(?:[-–,&]|and|or)?\s*)+)", re.I)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _made_this(kind: str, said: str, q, made: list[dict]) -> bool:
    """A reader recorded setting this field from words overlapping this clause."""
    a = _norm(said)
    return any(m["qid"] == q.qid and m["set"].startswith(_MADE_SET[kind])
               and (b := _norm(m["said"])) and (a in b or b in a) for m in made)


def _named_codes(said: str) -> set[int]:
    out: set[int] = set()
    for m in _CODES.finditer(said):
        for lo, _, hi in re.findall(r"(\d+)(\s*[-–]\s*(\d+))?", m.group(1)):
            out |= set(range(int(lo), int(hi) + 1)) if hi and int(hi) - int(lo) < 50 else {int(lo)}
    return out


def _consistent(kind: str, said: str, q) -> bool:
    """The note's own words agree with the field another reader set."""
    if kind == "base":
        refs = {r.upper() for e in [q.show_if, *(o.show_if for o in q.options or [])] if e
                for r in e.referenced_qids()}
        named = {n.upper() for n in _QIDS.findall(said)}
        return bool(named) and named <= refs
    if kind in ("anchor", "exclusive"):
        flagged = {o.code for o in q.options or []
                   if (o.anchored if kind == "anchor" else o.exclusive)}
        return _named_codes(said) <= flagged
    if kind == "range":
        v = _validation(q)
        return {int(n) for n in re.findall(r"\d+", said)} <= {v.get("min"), v.get("max")}
    return True


def _outcome(kind: str, said: str, q, variables: list[dict],
             qids: frozenset[str] = frozenset(), made: list[dict] = ()) -> tuple[str, str]:
    if kind == "reask":  # applied once the second ask exists; else a person decides
        return ("applied", f"{q.qid}_REASK") if f"{q.qid}_REASK" in qids else ("asked", "")
    if kind == "warning":  # kept as soft_message; the runtime shows no warnings yet
        return ("scripter", "") if "soft_message" in _validation(q) else ("unread", "")
    if kind in _FIXED:
        return _FIXED[kind], ""
    if kind == "randomize":
        if _TOGETHER.search(said):
            return "unread", "keeping groups together is not built"
        if _NEGATED.search(said):  # honoured when the question is not shuffled
            return ("unread", "still randomised") if q.randomize_options else ("applied", "randomize_options off")  # noqa: E501
        return ("applied", "randomize_options") if q.randomize_options else ("unread", "")
    if kind == "hidden_var":
        mine = [v for v in variables if v["at"] == q.qid]
        if not mine:
            return "unread", "no variable read from this note"
        if all(v["status"] == "ready" for v in mine):
            return "applied", ", ".join(v["name"] for v in mine)
        return "asked", "; ".join(v["why"] for v in mine if v["status"] != "ready")
    check = _CHECKS.get(kind)
    if kind in _MADE_SET:  # applied only if this note did it, or says what the field says
        if not (check and check(q, said)):
            return "unread", ""
        if _made_this(kind, said, q, list(made)):
            return "applied", ""
        ok = _consistent(kind, said, q)
        return ("applied", "set by the question's own reading") if ok else ("unread", "")
    return ("applied", "") if check and check(q, said) else ("unread", "")


def read_notes(survey: SurveyDefinition) -> list[dict[str, Any]]:
    variables = analyst_reading(survey)["variables"]
    out: list[dict[str, Any]] = []
    qids = frozenset(q.qid for q in survey.questions)
    made = (survey.metadata or {}).get("note_rules") or []
    by_qid = {q.qid: q for q in survey.questions}
    bands: dict[str, set[str]] = {}
    for v in variables:
        bands.setdefault(v["at"], set()).update(b["line"] for b in v.get("bands", []))
    for q in survey.questions:
        # Every sentence of a note turned into a second ask belongs to that flow.
        second = by_qid.get(q.qid + "_REASK")
        flow = second.extraction_evidence.get("flow", "") if second else ""
        for said in sentences(note_lines(q)):
            if not re.search(r"\w", said) or _NOT_A_NOTE.search(said):
                continue
            kinds = (["reask"] if said in flow
                     else [k for k, rx in KINDS if rx.search(said)] or ["none"])
            # A line listed under a "create … variable" note is that variable's group.
            if said in bands.get(q.qid, ()):
                kinds = ["hidden_var"]
            for kind in kinds:
                outcome, detail = ("unread", "") if kind == "none" else _outcome(kind, said, q, variables, qids, made)  # noqa: E501
                out.append({"qid": q.qid, "said": said, "kind": kind,
                            "outcome": outcome, "detail": detail})
    return out


def summary(records: list[dict]) -> dict[str, Any]:
    by_kind: dict[str, Counter] = {}
    for r in records:
        by_kind.setdefault(r["kind"], Counter())[r["outcome"]] += 1
    return {"total": len(records),
            "by_outcome": dict(Counter(r["outcome"] for r in records)),
            "by_kind": {k: dict(v) for k, v in by_kind.items()}}


def checklist(records: list[dict]) -> list[dict[str, str]]:
    """The scripter's checklist: each distinct scripter record once, uncapped."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for r in records:
        key = (r["kind"], r["qid"], r["said"])
        if r["outcome"] != "scripter" or key in seen:
            continue
        seen.add(key)
        out.append({"kind": r["kind"], "qid": r["qid"], "said": r["said"]})
    return out


def ledger(survey: SurveyDefinition) -> dict[str, Any]:
    records = read_notes(survey)
    return {"summary": summary(records), "records": records[:CAP],
            "checks": checklist(records)}
