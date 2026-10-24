"""What a senior researcher would say reading the questionnaire, while it is built.

Advisory, never blocking: each note names the question, says what is wrong in
plain words and what to do. Two weights — *fix* (the data will be biased or
unusable) and *consider* (worth a second look). Calibrated on a real B2B
questionnaire so a well-written survey is not buried in notes.

- Leading wording: the question hands over the answer.
- Two questions in one: "how satisfied are you with the price and quality".
- A multi-select with no exclusive "None of these".
- A scale that leans one way (more positive than negative points).
- A long unordered list shown in a fixed order (order bias).
- A long grid (fatigue, straight-lining).
- Many open questions; and the interview length against 20 minutes.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import GRID_TYPES, Question, QuestionType, SurveyDefinition

_LEADING = re.compile(
    r"\b(don'?t you|wouldn'?t you|isn'?t it|aren'?t they|surely|how much do you (love|enjoy|like)|"
    r"how (great|good|wonderful|amazing) (is|are|was|were))\b", re.I)
_EVALUATES = re.compile(r"\b(satisfied|satisfaction|rate|rating|agree|important|likely|"
                        r"happy|pleased|trust)\b", re.I)
_TWO_THINGS = re.compile(r"\b(\w{3,}) and (?:the |its |their )?(\w{3,})\b", re.I)
_NEGATIVE = re.compile(r"\b(dis\w+|un\w+|not|poor|bad|never|worse|less)\b", re.I)
_POSITIVE = re.compile(r"\b(very|extremely|completely|good|excellent|likely|satisfied|agree|"
                       r"always|better|more)\b", re.I)
_NONE = re.compile(r"\b(none|nothing|don'?t know|not sure|no one|neither)\b", re.I)
_ORDERED = re.compile(
    r"^\s*(\d|less than|more than|under|over|\$|₹|£|€|never|always|daily|weekly)", re.I)


def _say(q: Question, weight: str, note: str) -> dict[str, str]:
    return {"qid": q.qid, "weight": weight, "note": note}


def _shown(q: Question) -> bool:
    return q.type is not QuestionType.INFO and not q.qid.upper().startswith("HID")


def check_question(q: Question) -> list[dict[str, str]]:
    out = []
    text = q.text or ""
    labels = [o.label for o in q.options or []]
    if _LEADING.search(text):
        out.append(_say(q, "fix", "The wording suggests an answer. Ask neutrally: "
                                  "“How would you describe…”, “How satisfied or dissatisfied…”."))
    if q.type not in GRID_TYPES and _EVALUATES.search(text) and (m := _TWO_THINGS.search(text)) \
            and m.group(1).lower() not in ("you", "and") and len(text) < 160:
        out.append(_say(q, "consider", f"May ask two things at once (“{m.group(0)}”). Someone who "
                                       "feels differently about each cannot answer; split it."))
    if q.type is QuestionType.MULTI and labels and not any(
            o.exclusive or _NONE.search(o.label) for o in q.options):
        out.append(_say(q, "consider", "No “None of these” or “Don't know”. If some people have "
                                       "no answer, they will tick something anyway; add one, "
                                       "exclusive."))
    if q.type in (QuestionType.SINGLE, QuestionType.DROPDOWN) and 4 <= len(labels) <= 11:
        pos = sum(bool(_POSITIVE.search(x)) and not _NEGATIVE.search(x) for x in labels)
        neg = sum(bool(_NEGATIVE.search(x)) for x in labels)
        if pos >= 2 and neg >= 1 and abs(pos - neg) >= 2:
            out.append(_say(q, "fix", f"The scale leans one way ({pos} positive points, {neg} "
                                      "negative). Balance it around a neutral middle."))
    if q.type in (QuestionType.SINGLE, QuestionType.MULTI) and len(labels) >= 9 \
            and not q.randomize_options and not any(_ORDERED.search(x) for x in labels[:3]):
        out.append(_say(q, "consider", f"{len(labels)} answers in a fixed order: the first few get "
                                       "picked more. Randomise them (anchor “Other”/“None”)."))
    if q.type in GRID_TYPES and len(q.rows or []) > 12 and "MAXDIFF" not in q.qid.upper():
        out.append(_say(q, "consider", f"A grid of {len(q.rows)} rows invites straight-lining. "
                                       "Split it or keep the rows that matter."))
    return out


def check(survey: SurveyDefinition) -> dict[str, Any]:
    from src.engines.fielding.quote import estimate_length

    notes = [n for q in survey.questions if _shown(q) for n in check_question(q)]
    length = estimate_length(survey)
    overall = []
    if length["minutes"] > 20:
        overall.append({"qid": None, "weight": "consider",
                        "note": f"About {length['minutes']:.0f} minutes. Past 20, drop-out and "
                                "straight-lining rise; cut what the analysis will not use."})
    opens = [q for q in survey.questions if q.type is QuestionType.OPEN_TEXT and q.required
             and _shown(q)]
    if len(opens) > 4:
        overall.append({"qid": None, "weight": "consider",
                        "note": f"{len(opens)} required open questions. Each costs about half a "
                                "minute and answers thin out; make the less important optional."})
    return {"length": {k: length[k] for k in ("minutes", "low", "high")},
            "notes": overall + notes,
            "n_fix": sum(n["weight"] == "fix" for n in overall + notes),
            "n_consider": sum(n["weight"] == "consider" for n in overall + notes)}
