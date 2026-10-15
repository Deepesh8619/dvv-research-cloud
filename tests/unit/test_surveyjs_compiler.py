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
    display_warnings,
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
    # S2 randomizes, so the ORDER is per-compile (one shuffle per respondent) —
    # the content is not. `choicesOrder` is gone deliberately: SurveyJS cannot
    # pin an anchored option, so the server orders the list itself.
    assert sorted(s2["choices"], key=lambda c: c["value"]) == [
        {"value": 1, "text": "North"},
        {"value": 2, "text": "South"},
    ]
    assert "choicesOrder" not in s2
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


# --- what a respondent actually reads ----------------------------------------


def _with_techniques(question: Question, masks=None, pipes=None) -> Question:
    question.validation = {
        "techniques": {
            "masks": masks or [],
            "pipes": pipes or [],
            "least_fill": False,
        }
    }
    return question


def _brands(qid: str, text: str, **kw) -> Question:
    return Question(
        qid=qid,
        text=text,
        type=QuestionType.MULTI,
        options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3)],
        **kw,
    )


def _survey_of(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="s",
        title="t",
        questions=list(questions),
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
    )


def _elements(survey: SurveyDefinition) -> dict:
    return {
        el.get("name", "html"): el
        for page in compile_to_surveyjs(survey)["pages"]
        for el in page["elements"]
    }


def test_a_pipe_becomes_the_respondents_own_answer() -> None:
    """It used to reach the public as the instruction itself.

    The compiler assumed piping needed no work because SurveyJS interpolates
    "{qid}" markers "already present in question text". They are never present:
    a questionnaire writes the instruction in prose.
    """
    q2 = _brands("Q2", "Which brands do you use?")
    q3 = _with_techniques(
        Question(
            qid="Q3",
            text="What do you like about [INSERT RESPONSE FROM Q2]?",
            type=QuestionType.OPEN_TEXT,
        ),
        pipes=[{"source_qid": "Q2", "name": None, "verbatim": "INSERT RESPONSE FROM Q2"}],
    )

    assert _elements(_survey_of(q2, q3))["Q3"]["title"] == "What do you like about {Q2}?"


def test_a_mask_narrows_the_option_list() -> None:
    """Detected, stored, and then ignored — so everyone saw every option."""
    q1 = _brands("Q1", "Which brands are you aware of?")
    q2 = _with_techniques(
        _brands("Q2", "Which of those do you use?"),
        masks=[{"source_qid": "Q1", "mode": "selected", "exclude_qid": None, "verbatim": ""}],
    )

    assert _elements(_survey_of(q1, q2))["Q2"]["choicesVisibleIf"] == "{Q1} contains {item}"


def test_a_not_selected_mask_narrows_the_other_way() -> None:
    q1 = _brands("Q1", "Which brands are you aware of?")
    q2 = _with_techniques(
        _brands("Q2", "Which have you never tried?"),
        masks=[
            {"source_qid": "Q1", "mode": "not_selected", "exclude_qid": None, "verbatim": ""}
        ],
    )

    assert _elements(_survey_of(q1, q2))["Q2"]["choicesVisibleIf"] == "{Q1} notcontains {item}"


def test_a_mask_from_a_single_answer_question_compares_rather_than_contains() -> None:
    """`contains` is for a list. A single-select answer is one value."""
    q1 = Question(
        qid="Q1",
        text="Which brand do you use most?",
        type=QuestionType.SINGLE,
        options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3)],
    )
    q2 = _with_techniques(
        _brands("Q2", "And which others?"),
        masks=[
            {"source_qid": "Q1", "mode": "not_selected", "exclude_qid": None, "verbatim": ""}
        ],
    )

    assert _elements(_survey_of(q1, q2))["Q2"]["choicesVisibleIf"] == "{Q1} <> {item}"


def test_no_scripter_directive_survives_into_the_title() -> None:
    """Brackets are a scripting convention; parentheses are respondent language."""
    q = _brands("Q2", "Which of those do you use? [SHOW ONLY ITEMS SELECTED AT Q1]")
    q.options[2].label = "Other (Please specify): [ TXT ] ______"

    element = _elements(_survey_of(q))["Q2"]
    assert element["title"] == "Which of those do you use?"
    # The parenthesised half is the respondent's, and it stays.
    assert element["choices"][2]["text"] == "Other (Please specify): ______"


def test_a_pipe_we_cannot_resolve_is_removed_and_reported() -> None:
    """Nobody should read "[PHARMACY]". Nor should it ship unnoticed."""
    q = _with_techniques(
        Question(qid="Q4", text="Rate [PHARMACY] on value for money", type=QuestionType.OPEN_TEXT),
        pipes=[{"source_qid": None, "name": "PHARMACY", "verbatim": "PHARMACY"}],
    )
    survey = _survey_of(q)

    assert _elements(survey)["Q4"]["title"] == "Rate on value for money"
    warning = next(w for w in display_warnings(survey) if w["qid"] == "Q4")
    assert warning["kind"] == "unresolved_pipe"
    assert "PHARMACY" in warning["detail"]


def test_a_mask_whose_source_is_not_in_the_survey_is_reported() -> None:
    q = _with_techniques(
        _brands("Q2", "Which of those do you use?"),
        masks=[{"source_qid": "Q99", "mode": "selected", "exclude_qid": None, "verbatim": ""}],
    )
    survey = _survey_of(q)

    assert "choicesVisibleIf" not in _elements(survey)["Q2"]
    warning = next(w for w in display_warnings(survey) if w["qid"] == "Q2")
    assert warning["kind"] == "unenforceable_mask"
    assert "everyone will see every option" in warning["detail"]


def test_a_clean_survey_reports_nothing() -> None:
    q1 = _brands("Q1", "Which brands are you aware of?")
    assert display_warnings(_survey_of(q1)) == []


# --- the parts the script names -------------------------------------------


def _blocked_survey() -> SurveyDefinition:
    yes_no = [Option(code=1, label="Yes"), Option(code=2, label="No")]
    qids = ["S1", "GA1", "GA2", "M1", "M2", "Z9"]
    questions = [Question(qid=q, text=f"{q}?", options=list(yes_no)) for q in qids]
    survey = SurveyDefinition(
        study_id="T",
        questions=questions,
        blocks=[
            Block(block_id="blk_main", label="", question_ids=["S1"]),
            Block(block_id="blk_google_ads", label="Google Ads", question_ids=["GA1", "GA2"]),
            Block(block_id="blk_meta_ads", label="Meta Ads", question_ids=["M1", "M2"]),
            Block(block_id="blk_rest", label="", question_ids=["Z9"]),
        ],
    )
    survey.metadata["blocks_from_note"] = {
        "block_ids": ["blk_google_ads", "blk_meta_ads"],
    }
    return survey


def test_a_labelled_block_is_a_section_the_respondent_is_told_about():
    """The two arms the study exists to compare.

    A respondent carried from the Google Ads questions to the Meta Ads
    questions with nothing said has no idea the subject changed, and answers
    the second arm as if it were more of the first.
    """
    compiled = compile_to_surveyjs(_blocked_survey())
    assert compiled["sections"] == [
        {"page": "blk_google_ads", "label": "Google Ads"},
        {"page": "blk_meta_ads", "label": "Meta Ads"},
    ]


def test_an_unlabelled_block_is_never_announced_as_a_section():
    """"blk_main" is where everything else happens to sit.

    Naming it would be telling the respondent about a thing that is not one —
    and the section banner is only worth anything while it is rare.
    """
    compiled = compile_to_surveyjs(_blocked_survey())
    named = {s["page"] for s in compiled["sections"]}
    assert "blk_main" not in named and "blk_rest" not in named


def test_a_scripters_own_section_header_is_not_shown_to_a_respondent():
    """"Screener" and "Main" are the scaffolding, not the study.

    A block label is usually a header written for the person building the
    survey. Only a block the scripter note named as an arm of the comparison
    is in words the respondent's own questions use — everything else would be
    showing them how the script is organised.
    """
    compiled = compile_to_surveyjs(make_survey())
    assert [b.label for b in make_survey().blocks] == ["Screener", "Main"]
    assert "sections" not in compiled


def test_a_randomised_grid_shuffles_its_rows():
    from src.core.survey_definition import Option, Question, QuestionType
    from src.renderers.surveyjs_compiler import _compile_question

    q = Question(qid="G", text="Rate each", type=QuestionType.GRID_SINGLE, randomize_options=True,
                 rows=[Option(code=1, label="A"), Option(code=2, label="B")],
                 cols=[Option(code=1, label="Low"), Option(code=2, label="High")])
    element = _compile_question(q, {"G": q})
    assert element["rowsOrder"] == "random"
