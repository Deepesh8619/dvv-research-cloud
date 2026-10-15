"""Which reading skills decoded this questionnaire, and what none of them could.

Every reader records how it read a question (`extraction_method`, and the
fragment that decided each field in `extraction_evidence`). Per question, the
review screen shows that. Nothing showed it for the WHOLE document — so a
questionnaire written in a convention the system has never seen failed quietly,
one blank-looking question at a time.

This report is the document-level answer, written on every extraction:

* `skills`   — each skill that read something, with how many questions;
* `not_read` — questions no deterministic skill could read (AI or a person next);
* `teach`    — conventions the readers met and did not understand, counted
               across questions. A fragment seen on many questions is a client
               convention, and teaching it once (a literal pattern, no code)
               reads every later document that uses it.
* `verdict`  — "familiar" or "unfamiliar", so an unknown format is announced,
               not discovered question by question.

It reads what the readers already wrote; it changes nothing.
"""

from __future__ import annotations

import re
from collections import Counter

from src.core.survey_definition import SurveyDefinition

#: Every decoding skill: what it reads, and how a person extends it without code.
#: `method` matches `extraction_method`; `evidence` matches an evidence key.
SKILLS: list[dict] = [
    {"key": "spec_table", "method": "spec_table", "name": "Question tables",
     "reads": "one labelled table per question (Q Name / Q type / Among who / Other details)",
     "extend": "teach a row label: field_alias ('Asked of:' -> base)"},
    {"key": "prose", "method": "prose_directives", "name": "Written questions",
     "reads": "numbered or tagged questions with answer lines and [bracketed] notes",
     "extend": "teach a note: type_signal, option_tag, randomize_signal"},
    {"key": "loops", "method": "loop_expansion", "name": "Brand loops",
     "reads": "'loop through each brand' — one question per brand, each with its base",
     "extend": "automatic from the loop note and hidden variables"},
    {"key": "carousel", "method": "category_carousel", "name": "Per-category carousels",
     "reads": "a question asked once per category, brand list named once",
     "extend": "automatic"},
    {"key": "paths", "method": "section_path", "name": "Recruitment paths",
     "reads": "'<Path> (IF S0=1 AND S1=26-60) ASK P1-P3'", "extend": "automatic"},
    {"key": "intro", "method": "intro_screen", "name": "Conditional intro screens",
     "reads": "'[PN: SHOW IF P1=1]' over a paragraph", "extend": "automatic"},
    {"key": "split", "method": "grid_subquestion_split", "name": "Sub-question grids",
     "reads": "one grid holding several sub-questions", "extend": "automatic"},
    {"key": "bases", "evidence": "base", "name": "Who is asked (bases)",
     "reads": "Among who / [BASE: …] / ASK IF / section headings '(S0=2)'",
     "extend": "teach field_alias for a new base label"},
    {"key": "relative", "evidence": "show_if", "name": "Pointing bases",
     "reads": "'ANSWERED YES ABOVE', 'SELECTS 1-4 IN PREVIOUS QUESTION', loop turns",
     "extend": "automatic"},
    {"key": "screen_outs", "evidence": "terminate", "name": "Screen-outs",
     "reads": "TERMINATE / TERM / SCREEN OUT, per sentence, on codes or typed numbers",
     "extend": "teach screen_out_range or option_tag terminate"},
    {"key": "tables", "evidence": "options", "name": "Grids and fill-in tables",
     "reads": "rating grids, scale strips, stacked grids, per-row % tables",
     "extend": "automatic"},
    {"key": "word_codes", "evidence": "codes", "name": "Printed answer codes",
     "reads": "codes Word draws beside answers (list numbering)", "extend": "automatic"},
    {"key": "hidden", "evidence": "derived", "name": "Hidden variables",
     "reads": "'Show / HV: Hidden variable', HID_ names, CODE/RECODE lines",
     "extend": "teach hidden_variable ('SAMPLE SOURCE')"},
    {"key": "maxdiff", "evidence": "trade_off", "name": "MaxDiff / trade-off",
     "reads": "best-worst exercises", "extend": "automatic"},
    {"key": "auto_answer", "evidence": "auto_answer", "name": "Automatic answers",
     "reads": "'auto-punch those who select only one'", "extend": "automatic"},
]

#: A fragment met on at least this many questions is a convention, not a one-off.
_RECURRING = 2
#: Below this share read by a deterministic skill, the document is unfamiliar.
_FAMILIAR_SHARE = 0.85
_UNREAD_FLAGS = {"EXTRACTION_UNAVAILABLE", "EXTRACTION_CONFIG_ERROR", "EXTRACTION_MODEL_ERROR"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[\[\]]", " ", text or "").lower().split())


def _suggest(fragment: str, is_row_label: bool) -> str:
    """The pattern kind that would teach this fragment."""
    if is_row_label:
        return "field_alias"
    low = fragment.lower()
    if re.search(r"random|rotat|scramble|shuffle", low):
        return "randomize_signal"
    if re.search(r"terminat|exclusive|anchor|screen ?out", low):
        return "option_tag"
    if re.search(r"\bcode\b|recode|hidden|computed", low):
        return "hidden_variable"
    return "type_signal"


_EVERYONE = re.compile(r"^(?:base\s*:?\s*)?(?:ask\s+)?(?:of\s+)?all(?:\s+respondents)?$")
_RANGE_NOTE = re.compile(r"\d+\s*-\s*\d+|^%$|fill in|enter number|whole number")


def _understood(question, fragment: str, is_row_label: bool = False) -> bool:
    """Was this note acted on, even though no evidence field quotes it?

    "BASE: ALL" means everyone; "RANDOMIZE" set the order; "[0-100]" set the
    limits; a table's cell text is the table the grid reader already read.
    Offering these back as "teach me" would ask a person to teach what the
    system already does."""
    note = _norm(fragment)
    validation = question.validation or {}
    techniques = validation.get("techniques") or {}
    quoted = [_norm(t.get("verbatim", "")) for t in
              (techniques.get("masks") or []) + (techniques.get("pipes") or [])]
    quoted += [_norm(validation.get("routing_verbatim") or "")]
    quoted += [_norm(v) for v in (question.extraction_evidence or {}).values()]
    if any(note and (note in q or (len(q) > 12 and q in note)) for q in quoted if q):
        return True
    if note in ("oe", "open end") and question.type.value in ("numeric", "open_text"):
        return True
    if _EVERYONE.match(note):
        return True
    if "random" in note and not is_row_label and question.randomize_options:
        return True
    if _RANGE_NOTE.search(note) and ("min" in validation or "max" in validation):
        return True
    # A table line ("1 | 2 | 3", "COLUMNS: | COLUMNS:") once the table was read.
    return "|" in fragment and "[" not in fragment and bool(
        question.rows or question.cols or question.options)


def _unread(question) -> list[tuple[str, bool]]:
    """The fragments this question's reader met and did not act on."""
    validation = question.validation or {}
    found = [(str(d), False) for d in validation.get("scripter_directives") or []]
    found += [(str(r), True) for r in (validation.get("unrecognised_rows") or {})]
    return [(f, row) for f, row in found if _norm(f) and not _understood(question, f, row)]


def decoding_report(survey: SurveyDefinition) -> dict:
    """What decoded this document, what did not, and what to teach."""
    asked = [q for q in survey.questions if q.qid != "UNKNOWN"]
    skills = []
    for skill in SKILLS:
        if "method" in skill:
            hits = [q.qid for q in asked if q.extraction_method == skill["method"]]
        else:
            hits = [q.qid for q in asked if skill["evidence"] in (q.extraction_evidence or {})]
        if hits:
            skills.append({"key": skill["key"], "name": skill["name"], "questions": len(hits),
                           "examples": hits[:5]})
    not_read = [q.qid for q in asked
                if set(q.flags or ()) & _UNREAD_FLAGS
                or q.extraction_method in (None, "llm")]
    fragments: Counter = Counter()
    where: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for q in asked:
        for fragment, is_row in _unread(q):
            key = fragment.strip()
            fragments[key] += 1
            where.setdefault(key, []).append(q.qid)
            kinds[key] = _suggest(key, is_row)
    teach = [{"fragment": f, "questions": n, "examples": where[f][:4], "suggest": kinds[f]}
             for f, n in fragments.most_common() if n >= _RECURRING][:15]
    read_share = 1 - len(not_read) / len(asked) if asked else 0.0
    profile = (survey.metadata or {}).get("profile") or {}
    unknown_format = isinstance(profile, dict) and profile.get("slug") == "unknown"
    familiar = read_share >= _FAMILIAR_SHARE and not unknown_format
    return {
        "verdict": "familiar" if familiar else "unfamiliar",
        "questions": len(asked),
        "read_by_skills": len(asked) - len(not_read),
        "read_share": round(read_share, 3),
        "skills": skills,
        "not_read": not_read,
        "teach": teach,
        "format": profile.get("slug") if isinstance(profile, dict) else None,
    }
