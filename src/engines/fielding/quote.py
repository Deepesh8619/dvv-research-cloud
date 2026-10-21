"""A quote from the questionnaire itself: interview length, who qualifies, cost.

An agency's costliest guess is the bid. Length of interview, incidence and
cost per complete are estimated by hand from a Word file, and a study bid at
12 minutes that runs 17 is a study under-priced for every complete. DVV has
already read every question, type, base and screen-out, so it can estimate
the length before anyone scripts it and show which screeners decide who
qualifies.

Measured on the three real studies with interview times (2026-10-21):

    study        estimate   document's own   actual median
    Google Ads   22.0 min   12 min           17.3 min
    TRD1         13.0 min   15 min           19.9 min
    TRD2         12.4 min   15 min           12.4 min

Mean error 21% against the documents' 26% — better than the promise written
today, still a ±30% range, and shown as one. The constants are plain reading
and answering times, deliberately not fitted to three studies.

Prices come only from the user's own rate card; nothing here guesses what a
panel charges.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import Question, QuestionType, SurveyDefinition

READ_WPM = 200  # reading speed for survey text
ANSWER_SECONDS = 2.0  # deciding on one answer
OPTION_SECONDS = 0.6  # scanning one more option
ROW_SECONDS = 3.0  # answering one grid row
NUMBER_SECONDS = 6.0  # typing one number
OPEN_END_SECONDS = 25.0  # writing one open answer
MAXDIFF_SCREENS = 8  # a typical exercise when the design is not stated
MAXDIFF_SCREEN_SECONDS = 15.0
#: Share of respondents assumed to see a question asked of a subgroup.
CONDITIONAL_SHARE = 0.6
#: The range the estimate is shown with: its measured error.
RANGE = 0.3


def _words(text: str | None) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", text or ""))


def _read(*texts: str | None) -> float:
    return sum(_words(t) for t in texts) / READ_WPM * 60


def _is_hidden(q: Question) -> bool:
    return q.qid.upper().startswith("HID_") or "DERIVED_VARIABLE" in (q.flags or [])


def question_seconds(q: Question) -> float:
    """Seconds one respondent who is asked this question spends on it."""
    kind = q.type
    labels = " ".join(o.label for o in q.options or [])
    if kind is QuestionType.INFO:
        return _read(q.text)
    if kind in (QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN, QuestionType.RANK):
        return _read(q.text, labels) + ANSWER_SECONDS + OPTION_SECONDS * len(q.options or [])
    if kind in (QuestionType.GRID_SINGLE, QuestionType.GRID_MULTI):
        rows = q.rows or []
        return (_read(q.text, " ".join(r.label for r in rows)) + ANSWER_SECONDS
                + ROW_SECONDS * len(rows))
    if kind is QuestionType.NUMERIC:
        return _read(q.text) + NUMBER_SECONDS * max(1, len(q.rows or []))
    if kind is QuestionType.OPEN_TEXT:
        return _read(q.text) + OPEN_END_SECONDS
    if kind is QuestionType.MAXDIFF:
        screens = ((q.validation or {}).get("maxdiff") or {}).get("n_screens") or MAXDIFF_SCREENS
        return _read(q.text) + MAXDIFF_SCREEN_SECONDS * screens
    return _read(q.text) + ANSWER_SECONDS


def estimate_length(survey: SurveyDefinition) -> dict[str, Any]:
    """Interview length as a range, and the questions that take longest."""
    timed = []
    for q in survey.questions:
        if _is_hidden(q):
            continue  # computed by the script, never shown
        seconds = question_seconds(q) * (CONDITIONAL_SHARE if q.show_if is not None else 1.0)
        timed.append((q.qid, seconds))
    total = sum(s for _q, s in timed) / 60
    stated = ((survey.metadata or {}).get("study_overview") or {}).get("loi_minutes")
    return {
        "minutes": round(total, 1),
        "low": round(total * (1 - RANGE), 1),
        "high": round(total * (1 + RANGE), 1),
        "stated_minutes": stated if isinstance(stated, int) else None,
        "longest": [{"qid": q, "seconds": round(s)}
                    for q, s in sorted(timed, key=lambda t: -t[1])[:5]],
    }


def screeners(survey: SurveyDefinition) -> list[dict[str, Any]]:
    """The questions that screen people out, in the order a respondent meets them."""
    from src.engines.questionnaire.derived import rule_text

    out = []
    for q in survey.questions:
        if q.terminate_if is None:
            continue
        rule = (q.terminate_if.verbatim_text or "").strip() or rule_text(q.terminate_if)
        out.append({"qid": q.qid, "text": (q.text or "")[:140], "screens_out": rule[:140]})
    return out


def price(
    minutes: float,
    completes: int | None,
    incidence_pct: float | None,
    rate_per_minute: float | None,
    min_cpi: float | None = None,
    project_fee: float | None = None,
) -> dict[str, Any] | None:
    """Starts needed and sample cost, from the user's own rates. None without them."""
    if not completes or not incidence_pct or incidence_pct <= 0:
        return None
    incidence = incidence_pct / 100
    starts = round(completes / incidence)
    out: dict[str, Any] = {"completes": completes, "incidence_pct": incidence_pct,
                           "starts_needed": starts}
    if rate_per_minute:
        # Cost per complete scales with length and with how many people must
        # start to find one who qualifies.
        cpi = rate_per_minute * minutes / incidence
        cpi = max(cpi, min_cpi or 0)
        out["cost_per_complete"] = round(cpi, 2)
        out["sample_cost"] = round(cpi * completes, 2)
        out["total"] = round(out["sample_cost"] + (project_fee or 0), 2)
    return out


def quote(survey: SurveyDefinition, completes: int | None = None,
          incidence_pct: float | None = None, rate_per_minute: float | None = None,
          min_cpi: float | None = None, project_fee: float | None = None) -> dict[str, Any]:
    length = estimate_length(survey)
    overview = (survey.metadata or {}).get("study_overview") or {}
    completes = completes or overview.get("sample_size")
    return {
        "length": length,
        "screeners": screeners(survey),
        "cost": price(length["minutes"], completes, incidence_pct, rate_per_minute,
                      min_cpi, project_fee),
        "completes": completes,
        "n_questions": sum(1 for q in survey.questions
                           if not _is_hidden(q) and q.type is not QuestionType.INFO),
    }
