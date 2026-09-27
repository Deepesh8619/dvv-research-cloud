"""One stored question, twelve screens, and no two respondents alike."""

import pytest

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.maxdiff_screens import (
    apply_version,
    field_names,
    items_shown,
    problems,
)
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _radios(page):
    """The screen's two radio lists, from inside the panel that holds them."""
    return [
        element
        for group in page["elements"]
        for element in (group.get("elements") or [group])
        if element.get("type") == "radiogroup"
    ]


def _exercise(n_items=20, screens=12, per_screen=4, versions=10, alone=True):
    question = Question(
        qid="Q20",
        text="Which of these matters most?",
        type=QuestionType.MAXDIFF,
        options=[Option(code=i, label=f"Item {i}") for i in range(1, n_items + 1)],
        validation={
            "maxdiff": {
                "screens": screens,
                "items_per_screen": per_screen,
                "versions": versions,
            }
        },
    )
    extra = [] if alone else [Question(qid="Q21", text="Age?", type=QuestionType.NUMERIC)]
    return SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="blk_md", question_ids=["Q20", *(q.qid for q in extra)])],
        questions=[question, *extra],
    )


def test_one_question_becomes_one_page_per_screen():
    compiled = compile_to_surveyjs(_exercise())
    assert len(compiled["pages"]) == 12
    assert [p["name"] for p in compiled["pages"][:2]] == ["blk_md_s1", "blk_md_s2"]
    assert compiled["pages"][0]["maxdiffScreen"] == {"qid": "Q20", "index": 0}


def test_the_choices_compile_empty_so_a_missed_version_fails_loudly():
    """Filling them with version 0 would mean that if the per-respondent pass
    ever stopped running, every respondent silently took the same twelve
    screens — the design gone, the data still arriving, nothing to see."""
    compiled = compile_to_surveyjs(_exercise())
    assert all(
        element["choices"] == []
        for page in compiled["pages"]
        for element in _radios(page)
    )


def test_applying_a_version_fills_every_screen():
    survey = _exercise()
    compiled = apply_version(compile_to_surveyjs(survey), survey, version=3)
    for page in compiled["pages"]:
        for element in _radios(page):
            assert len(element["choices"]) == 4
            assert len({c["value"] for c in element["choices"]}) == 4
    # Best and worst offer the same four items — it is one screen.
    best, worst = _radios(compiled["pages"][0])
    assert best["choices"] == worst["choices"]


def test_two_respondents_do_not_see_the_same_screens():
    """The whole point of the exercise: each person is shown a different subset."""
    survey = _exercise()
    a = apply_version(compile_to_surveyjs(survey), survey, version=0)
    b = apply_version(compile_to_surveyjs(survey), survey, version=1)
    same = [
        _radios(a["pages"][i])[0]["choices"] == _radios(b["pages"][i])[0]["choices"]
        for i in range(12)
    ]
    assert not all(same)


def test_the_screen_refuses_the_same_item_as_best_and_worst():
    compiled = compile_to_surveyjs(_exercise())
    worst = _radios(compiled["pages"][0])[1]
    assert worst["name"] == "Q20_s1_worst"
    assert worst["validators"][0]["expression"] == (
        "{Q20_s1_worst} empty or {Q20_s1_worst} <> {Q20_s1_best}"
    )


def test_items_shown_speaks_option_codes():
    """The design works in positions; everything else in the platform — routing,
    the data map, the tabs — speaks option codes."""
    survey = _exercise()
    codes = items_shown(survey["Q20"], version=0, screen=0)
    assert len(codes) == 4
    assert all(1 <= code <= 20 for code in codes)
    assert field_names("Q20", 0) == ("Q20_s1_best", "Q20_s1_worst")


def test_an_exercise_with_no_design_is_reported_not_guessed():
    question = Question(
        qid="Q20", text="t", type=QuestionType.MAXDIFF,
        options=[Option(code=1, label="a")],
    )
    found = problems(question)
    assert [severity for severity, _ in found] == ["fatal"]
    assert "states no MaxDiff design" in found[0][1]


def test_an_exercise_with_no_items_is_reported():
    question = Question(
        qid="Q20", text="t", type=QuestionType.MAXDIFF,
        validation={"maxdiff": {"screens": 12, "items_per_screen": 4}},
    )
    assert problems(question) == [("fatal", "has no items to compare")]


def test_an_exercise_sharing_a_page_is_left_alone_for_preflight_to_refuse():
    """Silently splitting somebody's page around their other questions would be
    a worse surprise than refusing the link and saying why."""
    compiled = compile_to_surveyjs(_exercise(alone=False))
    assert len(compiled["pages"]) == 1
    assert compiled["pages"][0]["elements"][0]["type"] == "html"


def test_applying_a_version_raises_rather_than_serving_an_empty_screen():
    survey = _exercise()
    compiled = compile_to_surveyjs(survey)
    survey.questions[0].options = []
    with pytest.raises(ValueError, match="no items to show"):
        apply_version(compiled, survey, version=0)


def test_both_halves_of_a_screen_stay_on_one_page():
    """The respondent page runs SurveyJS in `questionPerPage`, which renders ONE
    question at a time — and that split the screen in half: they picked a best,
    advanced, and were asked for the worst of a list they could no longer see.

    Best and worst are a single simultaneous judgement over one set of four.
    Separated they are two different questions, and the utilities are not what
    the exercise measured. A panel is one element, so the pair stays together.
    """
    page = compile_to_surveyjs(_exercise())["pages"][0]
    assert [e["type"] for e in page["elements"]] == ["panel"]
    assert [e["name"] for e in page["elements"][0]["elements"]] == [
        "Q20_s1_best",
        "Q20_s1_worst",
    ]
