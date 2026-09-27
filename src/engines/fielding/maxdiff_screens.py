"""A MaxDiff question, as the respondent meets it: many screens, not one.

One stored question becomes twelve screens, each showing four of the twenty
items and asking for the best and the worst of those four. Which four depends
on the respondent's design version, so the compiled survey cannot contain the
answer lists — they are filled in per respondent at entry, the same way block
rotation reorders pages there.

Three decisions worth knowing about:

**The choices compile EMPTY.** Filling them with version 0 and replacing them
later would mean that if the per-respondent pass ever failed to run, every
respondent silently took the same twelve screens — the design gone, the data
still arriving, nothing to see. Empty choices fail loudly instead: a screen
with nothing to pick is obvious in a preview and impossible to miss in a test.

**A MaxDiff question is alone on its page.** One task per screen is what the
exercise is for, and a block holding only the exercise is how documents write
it. Preflight says so rather than this module silently splitting a page around
other questions.

**Best and worst are two questions, not a grid.** SurveyJS has no per-column
matrix, and the alternative is a custom widget. Two radio lists of four items
is eight buttons on screen and entirely usable.
    # ponytail: the classic Most | item | Least table needs a custom widget;
    # two radio groups until somebody minds.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.fielding.maxdiff_design import (
    FATAL,
    MaxDiffDesign,
    build,
    check_parameters,
)

#: Where the design parameters live on the question.
SPEC_KEY = "maxdiff"
DEFAULT_VERSIONS = 10
BEST, WORST = "best", "worst"


def spec(question: Question) -> dict[str, int] | None:
    """The design parameters this question declares, or None if it is not one."""
    if question.type is not QuestionType.MAXDIFF:
        return None
    raw = (question.validation or {}).get(SPEC_KEY) or {}
    screens, per_screen = raw.get("screens"), raw.get("items_per_screen")
    if not isinstance(screens, int) or not isinstance(per_screen, int):
        return None
    versions = raw.get("versions")
    return {
        "screens": screens,
        "items_per_screen": per_screen,
        "versions": versions if isinstance(versions, int) and versions > 0
        else DEFAULT_VERSIONS,
    }


def questions_in(survey: SurveyDefinition) -> list[Question]:
    return [q for q in survey.questions if q.type is QuestionType.MAXDIFF]


@lru_cache(maxsize=32)
def _cached_design(
    n_items: int, screens: int, per_screen: int, versions: int
) -> MaxDiffDesign:
    """Designs are deterministic, so one per parameter set serves every
    respondent — building one per entry would put half a second on the door."""
    return build(n_items, screens, per_screen, n_versions=versions)


def design_for(question: Question) -> MaxDiffDesign | None:
    params = spec(question)
    if params is None or not question.options:
        return None
    if _fatal(question, params):
        return None
    return _cached_design(
        len(question.options), params["screens"], params["items_per_screen"],
        params["versions"],
    )


def _fatal(question: Question, params: dict[str, int]) -> bool:
    return any(
        severity == FATAL
        for severity, _ in check_parameters(
            len(question.options or []), params["screens"], params["items_per_screen"]
        )
    )


def problems(question: Question) -> list[tuple[str, str]]:
    """(severity, why) for everything wrong with this exercise. Empty means fine.

    An exercise too thin to read is ADVISORY, not FATAL — how many screens a
    study can ask of a respondent is a research judgement, and refusing the
    link over it would be this module overruling the person who wrote the
    questionnaire.
    """
    if question.type is not QuestionType.MAXDIFF:
        return []
    params = spec(question)
    if params is None:
        return [(
            FATAL,
            "states no MaxDiff design — it needs a number of screens and items "
            "per screen before anybody can be shown it",
        )]
    if not question.options:
        return [(FATAL, "has no items to compare")]
    return check_parameters(
        len(question.options), params["screens"], params["items_per_screen"]
    )


def field_names(qid: str, screen: int) -> tuple[str, str]:
    """The two answers one screen captures."""
    return f"{qid}_s{screen + 1}_{BEST}", f"{qid}_s{screen + 1}_{WORST}"


def page_name(block_id: str, screen: int) -> str:
    return f"{block_id}_s{screen + 1}"


def _screen_page(
    question: Question, block_page: dict[str, Any], screen: int, title: str
) -> dict[str, Any]:
    best, worst = field_names(question.qid, screen)
    # Wrapped in a panel because the respondent page runs SurveyJS in
    # `questionPerPage`, which renders ONE question at a time — and that split
    # the screen in half: the respondent picked a best, advanced, and was asked
    # for the worst of a list they could no longer see. Best and worst are a
    # single simultaneous judgement over one set of four; separated, they are
    # two different questions and the utilities are not what the exercise
    # measured. A panel is one element, so the pair stays together.
    page: dict[str, Any] = {
        "name": page_name(block_page["name"], screen),
        "elements": [{
            "type": "panel",
            "name": f"{page_name(block_page['name'], screen)}_pair",
            "elements": [
            {
                "type": "radiogroup",
                "name": best,
                "title": "Most important",
                "choices": [],
                "isRequired": True,
            },
            {
                "type": "radiogroup",
                "name": worst,
                "title": "Least important",
                "choices": [],
                "isRequired": True,
                # The one rule the screen itself enforces. Best and worst being
                # the same item is not an opinion, it is a mis-click — and the
                # server refuses it too, because this one runs in the
                # respondent's browser.
                "validators": [
                    {
                        "type": "expression",
                        "expression": f"{{{worst}}} empty or {{{worst}}} <> {{{best}}}",
                        "text": "Please pick a different item as least important.",
                    }
                ],
            },
            ],
        }],
        "title": title,
        # Read by the respondent page; SurveyJS ignores what it does not know.
        "maxdiffScreen": {"qid": question.qid, "index": screen},
    }
    if "visibleIf" in block_page:
        page["visibleIf"] = block_page["visibleIf"]
    return page


def expand_pages(
    survey: SurveyDefinition, pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace each MaxDiff question's page with one page per screen.

    A page holding anything besides the exercise is left exactly as it is;
    preflight is what tells the reviewer about it, because silently splitting
    somebody's page around their other questions would be a worse surprise.
    """
    by_block = {
        block.block_id: block.question_ids for block in survey.blocks
    }
    out: list[dict[str, Any]] = []
    for page in pages:
        qids = by_block.get(page["name"]) or []
        exercise = next(
            (
                survey[qid]
                for qid in qids
                if qid in survey and survey[qid].type is QuestionType.MAXDIFF
            ),
            None,
        )
        design = design_for(exercise) if exercise is not None else None
        if exercise is None or design is None or len(qids) != 1:
            out.append(page)
            continue
        title = page.get("title") or exercise.text or ""
        out.extend(
            _screen_page(exercise, page, screen, title)
            for screen in range(design.n_screens)
        )
    return out


def items_shown(question: Question, version: int, screen: int) -> list[int]:
    """The option CODES this respondent was shown on one screen.

    Codes, not indices: the design works in positions into the item list, and
    everything else in the platform — routing, the data map, the tabs — speaks
    option codes. Translating here keeps the design engine free of that.
    """
    design = design_for(question)
    if design is None:
        return []
    options = question.options or []
    return [
        options[i].code
        for i in design.screens_for(version % design.n_versions)[screen]
        if i < len(options) and options[i].code is not None
    ]


def apply_version(
    compiled: dict[str, Any], survey: SurveyDefinition, version: int
) -> dict[str, Any]:
    """Fill every MaxDiff screen with the items THIS respondent sees.

    Raises if a screen is left empty. A respondent served a screen with nothing
    on it is a bug that must stop here rather than arrive as a dropout nobody
    can explain.
    """
    exercises = {q.qid: q for q in questions_in(survey)}
    if not exercises:
        return compiled

    for page in compiled.get("pages", []):
        marker = page.get("maxdiffScreen")
        if not marker:
            continue
        question = exercises.get(marker["qid"])
        if question is None:
            raise ValueError(f"screen for unknown question {marker['qid']!r}")
        codes = items_shown(question, version, marker["index"])
        if not codes:
            raise ValueError(
                f"{marker['qid']} screen {marker['index'] + 1} has no items to show"
            )
        labels = {o.code: o.label for o in question.options or []}
        choices = [{"value": code, "text": labels.get(code, str(code))} for code in codes]
        for element in _radiogroups(page["elements"]):
            element["choices"] = list(choices)
    return compiled


def _radiogroups(elements: list[dict[str, Any]]):
    """The screen's two radio lists, wherever the panel puts them."""
    for element in elements:
        if element.get("type") == "panel":
            yield from _radiogroups(element.get("elements") or [])
        elif element.get("type") == "radiogroup":
            yield element
