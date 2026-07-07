"""SurveyJS compiler: SurveyDefinition -> SurveyJS survey JSON.

Builds a realistic multi-block instrument (screener with a show_if-gated
question, a grid, a multi-select, an info block) and asserts on the *shape* of
the compiled SurveyJS model — page count, element types, choices, and
`visibleIf` condition strings — rather than a full golden diff.
"""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.renderers.surveyjs_compiler import (
    _expression_to_visibleif_string,
    compile_to_surveyjs,
)


def make_survey() -> SurveyDefinition:
    intro = Question(qid="INTRO", text="Welcome to the study.", type=QuestionType.INFO)
    s1 = Question(
        qid="S1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        validation={"min": 18, "max": 99},
    )
    s2 = Question(
        qid="S2",
        text="Which region?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="North"), Option(code=2, label="South")],
        randomize_options=True,
    )
    q1 = Question(
        qid="Q1",
        text="Which brands are you aware of?",
        type=QuestionType.MULTI,
        options=[
            Option(code=1, label="Brand A"),
            Option(code=2, label="Brand B"),
            Option(code=99, label="None", exclusive=True),
        ],
    )
    q2 = Question(
        qid="Q2",
        text="Rate each brand.",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="Brand A"), Option(code=2, label="Brand B")],
        cols=[Option(code=1, label="Poor"), Option(code=2, label="Good")],
    )
    q3 = Question(
        qid="Q3",
        text="Why do you say that about {S2}?",
        type=QuestionType.OPEN_TEXT,
        required=False,
        piping_refs=["S2"],
        show_if=Expression(op="eq", qid="S2", value=1),
    )
    return SurveyDefinition(
        study_id="ST-1",
        title="Demo Study",
        blocks=[
            Block(
                block_id="screener",
                label="Screener",
                question_ids=["INTRO", "S1", "S2"],
                randomize_questions=True,
            ),
            Block(block_id="main", label="Main", question_ids=["Q1", "Q2", "Q3"]),
        ],
        questions=[intro, s1, s2, q1, q2, q3],
    )


def _element(page: dict, name: str) -> dict:
    return next(e for e in page["elements"] if e.get("name") == name)


def test_top_level_shape():
    out = compile_to_surveyjs(make_survey())
    assert out["title"] == "Demo Study"
    assert [p["name"] for p in out["pages"]] == ["screener", "main"]


def test_page_titles_and_randomization():
    pages = compile_to_surveyjs(make_survey())["pages"]
    screener = pages[0]
    assert screener["title"] == "Screener"
    assert screener["questionsOrder"] == "random"
    assert "questionsOrder" not in pages[1]


def test_info_element_has_no_name_or_required():
    screener = compile_to_surveyjs(make_survey())["pages"][0]
    html_el = next(e for e in screener["elements"] if e["type"] == "html")
    assert html_el["html"] == "Welcome to the study."
    assert "name" not in html_el
    assert "isRequired" not in html_el


def test_single_and_multi_choices():
    pages = compile_to_surveyjs(make_survey())["pages"]
    s2 = _element(pages[0], "S2")
    assert s2["type"] == "radiogroup"
    assert s2["choices"] == [
        {"value": 1, "text": "North"},
        {"value": 2, "text": "South"},
    ]
    assert s2["choicesOrder"] == "random"  # randomize_options
    q1 = _element(pages[1], "Q1")
    assert q1["type"] == "checkbox"
    assert {"value": 99, "text": "None"} in q1["choices"]


def test_numeric_element_and_validators():
    s1 = _element(compile_to_surveyjs(make_survey())["pages"][0], "S1")
    assert s1["type"] == "text"
    assert s1["inputType"] == "number"
    assert s1["validators"] == [{"type": "numeric", "minValue": 18, "maxValue": 99}]


def test_grid_matrix_rows_and_columns():
    q2 = _element(compile_to_surveyjs(make_survey())["pages"][1], "Q2")
    assert q2["type"] == "matrix"
    assert q2["rows"] == [
        {"value": 1, "text": "Brand A"},
        {"value": 2, "text": "Brand B"},
    ]
    assert q2["columns"] == [
        {"value": 1, "text": "Poor"},
        {"value": 2, "text": "Good"},
    ]


def test_open_text_comment_and_required_flag():
    q3 = _element(compile_to_surveyjs(make_survey())["pages"][1], "Q3")
    assert q3["type"] == "comment"  # no length constraint -> multi-line
    assert q3["isRequired"] is False


def test_open_text_short_when_maxlength():
    q = Question(
        qid="OX",
        text="Postcode?",
        type=QuestionType.OPEN_TEXT,
        validation={"max_length": 8},
    )
    survey = SurveyDefinition(
        study_id="ST",
        blocks=[Block(block_id="b", question_ids=["OX"])],
        questions=[q],
    )
    el = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    assert el["type"] == "text"
    assert el["maxLength"] == 8


def test_show_if_becomes_visibleif_string():
    q3 = _element(compile_to_surveyjs(make_survey())["pages"][1], "Q3")
    assert q3["visibleIf"] == "{S2} = 1"


def test_piping_marker_preserved_in_title():
    q3 = _element(compile_to_surveyjs(make_survey())["pages"][1], "Q3")
    assert q3["title"] == "Why do you say that about {S2}?"


def test_dropdown_and_ranking_types():
    dd = Question(
        qid="D",
        text="Pick",
        type=QuestionType.DROPDOWN,
        options=[Option(code=1, label="A")],
    )
    rk = Question(
        qid="R",
        text="Rank",
        type=QuestionType.RANK,
        options=[Option(code=1, label="A"), Option(code=2, label="B")],
    )
    survey = SurveyDefinition(
        study_id="ST",
        blocks=[Block(block_id="b", question_ids=["D", "R"])],
        questions=[dd, rk],
    )
    page = compile_to_surveyjs(survey)["pages"][0]
    assert _element(page, "D")["type"] == "dropdown"
    assert _element(page, "R")["type"] == "ranking"


def test_block_show_if_becomes_page_visibleif():
    q = Question(qid="Q", text="hi", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Yes")])
    survey = SurveyDefinition(
        study_id="ST",
        blocks=[
            Block(
                block_id="gated",
                question_ids=["Q"],
                show_if=Expression(op="gt", qid="AGE", value=18),
            )
        ],
        questions=[q],
    )
    page = compile_to_surveyjs(survey)["pages"][0]
    assert page["visibleIf"] == "{AGE} > 18"


def test_terminate_if_is_not_emitted():
    q = Question(
        qid="AGE",
        text="Age?",
        type=QuestionType.NUMERIC,
        terminate_if=Expression(op="lt", qid="AGE", value=18),
    )
    survey = SurveyDefinition(
        study_id="ST",
        blocks=[Block(block_id="b", question_ids=["AGE"])],
        questions=[q],
    )
    el = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    # terminate is server-authoritative; nothing about it leaks into SurveyJS.
    assert "visibleIf" not in el
    assert "terminate" not in str(el).lower()


# --- visibleIf string translation ------------------------------------------


def test_visibleif_leaf_operators():
    assert _expression_to_visibleif_string(
        Expression(op="eq", qid="Q", value=1)) == "{Q} = 1"
    assert _expression_to_visibleif_string(
        Expression(op="ne", qid="Q", value=2)) == "{Q} <> 2"
    assert _expression_to_visibleif_string(
        Expression(op="gt", qid="Q", value=3)) == "{Q} > 3"
    assert _expression_to_visibleif_string(
        Expression(op="lt", qid="Q", value=4)) == "{Q} < 4"


def test_visibleif_selected_and_in():
    assert _expression_to_visibleif_string(
        Expression(op="selected", qid="Q", value=2)) == "{Q} contains 2"
    assert _expression_to_visibleif_string(
        Expression(op="in", qid="Q", values=[1, 2])) == "{Q} anyof [1, 2]"


def test_visibleif_string_value_is_quoted():
    assert _expression_to_visibleif_string(
        Expression(op="eq", qid="Q", value="yes")) == "{Q} = 'yes'"


def test_visibleif_nested_logical_tree():
    expr = Expression(
        op="or",
        children=[
            Expression(op="eq", qid="S2", value=1),
            Expression(op="and", children=[
                Expression(op="in", qid="S2", values=[2]),
                Expression(op="not", children=[Expression(op="lt", qid="S1", value=25)]),
            ]),
        ],
    )
    result = _expression_to_visibleif_string(expr)
    assert result == "({S2} = 1) or (({S2} anyof [2]) and (not (({S1} < 25))))"
