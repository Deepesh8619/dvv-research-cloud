"""Survey instrument model: construction, JSON round-trip, validation.

Fixtures build a small but realistic instrument — a screener block with a
terminate condition and a main block with a grid question — then deliberately
break it to prove each validation rule fires.
"""

import pytest

from src.core.survey_definition import (
    Block,
    EmbeddedField,
    Expression,
    FlowNode,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    QuotaSheet,
    SurveyDefinition,
    evaluate,
    selected_codes,
)


def make_survey() -> SurveyDefinition:
    """S1 (age) -> terminate under-18; S2 (region single); Q1 grid; Q2 shown via and/or."""
    s1 = Question(
        qid="S1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        options=[],
        terminate_if=Expression(
            op="lt",
            qid="S1",
            value=18,
            verbatim_text="Terminate if respondent is under 18.",
        ),
        source_text="Q. What is your age? ___",
        source_location="p.1 para.2",
        extraction_confidence={"text": 0.98, "type": 0.91},
    )
    s2 = Question(
        qid="S2",
        text="Which region do you live in?",
        type=QuestionType.SINGLE,
        options=[
            Option(code=1, label="North"),
            Option(code=2, label="South"),
            Option(code=99, label="None of these", exclusive=True),
        ],
    )
    q1 = Question(
        qid="Q1",
        text="Rate each brand.",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="Brand A"), Option(code=2, label="Brand B")],
        cols=[Option(code=1, label="Poor"), Option(code=2, label="Good")],
    )
    q2 = Question(
        qid="Q2",
        text="Follow-up.",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
        show_if=Expression(
            op="or",
            children=[
                Expression(op="eq", qid="S2", value=1),
                Expression(op="and", children=[
                    Expression(op="in", qid="S2", values=[2]),
                    Expression(op="not", children=[Expression(op="lt", qid="S1", value=25)]),
                ]),
            ],
            verbatim_text="Ask if North, or South and 25+.",
        ),
    )
    return SurveyDefinition(
        study_id="ST-100",
        title="Demo Study",
        source="extracted",
        status="draft",
        blocks=[
            Block(block_id="screener", label="Screener", question_ids=["S1", "S2"]),
            Block(block_id="main", label="Main", question_ids=["Q1", "Q2"]),
        ],
        flow=[
            FlowNode(type="block", block_id="screener"),
            FlowNode(type="block", block_id="main"),
            FlowNode(type="end", status="complete"),
        ],
        quotas=[
            QuotaSheet(
                name="region",
                dimensions=[
                    {
                        "cell_label": "North",
                        "condition": Expression(op="eq", qid="S2", value=1),
                        "limit": 100,
                        "mode": "hard",
                    }
                ],
            )
        ],
        embedded_data=[EmbeddedField(name="src", source="url_param", default="web")],
        questions=[s1, s2, q1, q2],
        metadata={"source_file": "brief.docx", "extraction_model": "opus"},
    )


def test_construct_and_index():
    survey = make_survey()
    assert "S1" in survey
    assert survey["S1"].terminate_if is not None
    assert survey.question_order() == ["S1", "S2", "Q1", "Q2"]
    assert survey.validate() == []


def test_json_round_trip_preserves_everything():
    survey = make_survey()
    restored = SurveyDefinition.from_json(survey.to_json())
    assert restored.to_dict() == survey.to_dict()
    # Nested expression survived intact.
    q2 = restored["Q2"]
    assert q2.show_if is not None
    assert q2.show_if.op == "or"
    assert q2.show_if.referenced_qids() == {"S1", "S2"}
    # Grid rows/cols and provenance survived.
    assert [o.label for o in restored["Q1"].rows] == ["Brand A", "Brand B"]
    assert restored["S1"].extraction_confidence == {"text": 0.98, "type": 0.91}
    # Enum type reconstructed, not left as a string.
    assert restored["Q1"].type is QuestionType.GRID_SINGLE
    # Quota condition rehydrated to an Expression.
    assert isinstance(restored.quotas[0].dimensions[0]["condition"], Expression)


def test_referenced_qids_walks_nested_tree():
    expr = Expression(
        op="and",
        children=[
            Expression(op="eq", qid="A", value=1),
            Expression(op="or", children=[
                Expression(op="in", qid="B", values=[1, 2]),
                Expression(op="not", children=[Expression(op="selected", qid="C", value=3)]),
            ]),
        ],
    )
    assert expr.referenced_qids() == {"A", "B", "C"}
    assert Expression.from_dict(expr.to_dict()).referenced_qids() == {"A", "B", "C"}


def test_validate_catches_duplicate_qid():
    survey = make_survey()
    survey.questions.append(Question(qid="S1", text="dup", type=QuestionType.INFO))
    problems = survey.validate()
    assert any("duplicate qid: S1" in p for p in problems)


def test_validate_catches_duplicate_option_code():
    survey = make_survey()
    survey["S2"].options.append(Option(code=1, label="Duplicate"))
    problems = survey.validate()
    assert any("duplicate option code 1" in p for p in problems)


def test_validate_catches_unknown_referenced_qid():
    survey = make_survey()
    survey["S2"].show_if = Expression(op="eq", qid="ZZ", value=1)
    problems = survey.validate()
    assert any("references unknown qid ZZ" in p for p in problems)


def test_validate_catches_forward_reference():
    survey = make_survey()
    # S1 (first) referencing Q1 (later) is a forward reference.
    survey["S1"].show_if = Expression(op="eq", qid="Q1", value=1)
    problems = survey.validate()
    assert any("forward-references Q1" in p for p in problems)


def test_validate_catches_empty_grid_rows():
    survey = make_survey()
    survey["Q1"].rows = None
    problems = survey.validate()
    assert any("grid_single requires non-empty rows" in p for p in problems)


# --- Expression.evaluate ---------------------------------------------------


def test_evaluate_eq_and_ne():
    assert evaluate(Expression(op="eq", qid="Q", value=1), {"Q": 1}) is True
    assert evaluate(Expression(op="eq", qid="Q", value=1), {"Q": 2}) is False
    assert evaluate(Expression(op="ne", qid="Q", value=1), {"Q": 2}) is True
    assert evaluate(Expression(op="ne", qid="Q", value=1), {"Q": 1}) is False


def test_evaluate_gt_and_lt():
    assert evaluate(Expression(op="gt", qid="A", value=18), {"A": 21}) is True
    assert evaluate(Expression(op="gt", qid="A", value=18), {"A": 18}) is False
    assert evaluate(Expression(op="lt", qid="A", value=18), {"A": 17}) is True
    assert evaluate(Expression(op="lt", qid="A", value=18), {"A": 18}) is False


def test_evaluate_in_single_and_multi():
    expr = Expression(op="in", qid="Q", values=[2, 3])
    assert evaluate(expr, {"Q": 2}) is True  # single-select scalar
    assert evaluate(expr, {"Q": 1}) is False
    assert evaluate(expr, {"Q": [1, 3]}) is True  # multi-select list intersects
    assert evaluate(expr, {"Q": [1, 4]}) is False


def test_evaluate_selected_multi():
    expr = Expression(op="selected", qid="Q", value=3)
    assert evaluate(expr, {"Q": [1, 3, 5]}) is True
    assert evaluate(expr, {"Q": [1, 5]}) is False
    # `selected` also works against a single-select scalar answer.
    assert evaluate(expr, {"Q": 3}) is True
    assert evaluate(expr, {"Q": 2}) is False


def test_evaluate_nested_and_or_not():
    # (S2 == 1) OR (S2 in [2] AND NOT (S1 < 25))
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
    assert evaluate(expr, {"S2": 1, "S1": 40}) is True   # first branch
    assert evaluate(expr, {"S2": 2, "S1": 30}) is True   # second branch, 30 >= 25
    assert evaluate(expr, {"S2": 2, "S1": 20}) is False  # 20 < 25 fails NOT
    assert evaluate(expr, {"S2": 3, "S1": 40}) is False  # neither branch


def test_evaluate_missing_answer_is_not_satisfied():
    # Absent qid -> False, never raises (routing degrades on incomplete data).
    assert evaluate(Expression(op="eq", qid="Q", value=1), {}) is False
    assert evaluate(Expression(op="gt", qid="Q", value=1), {}) is False
    assert evaluate(Expression(op="selected", qid="Q", value=1), {}) is False
    assert evaluate(Expression(op="in", qid="Q", values=[1]), {}) is False
    # None-valued answer is also treated as unanswered.
    assert evaluate(Expression(op="eq", qid="Q", value=1), {"Q": None}) is False


def test_evaluate_missing_answer_inside_logical_tree():
    expr = Expression(op="and", children=[
        Expression(op="eq", qid="A", value=1),
        Expression(op="eq", qid="B", value=1),  # B unanswered
    ])
    assert evaluate(expr, {"A": 1}) is False
    or_expr = Expression(op="or", children=[
        Expression(op="eq", qid="A", value=1),
        Expression(op="eq", qid="B", value=1),  # B unanswered but A satisfies
    ])
    assert evaluate(or_expr, {"A": 1}) is True


def test_evaluate_unknown_op_raises():
    with pytest.raises(ValueError):
        evaluate(Expression(op="bogus", qid="Q", value=1), {"Q": 1})


def test_evaluate_matches_survey_show_if():
    # The real show_if from make_survey() should gate correctly on live answers.
    survey = make_survey()
    show_if = survey["Q2"].show_if
    assert evaluate(show_if, {"S2": 1, "S1": 40}) is True
    assert evaluate(show_if, {"S2": 2, "S1": 20}) is False


def test_question_variable_link_round_trip():
    link = QuestionVariableLink(
        qid="Q5",
        variable_names=["Q5_1", "Q5_2", "Q5_3"],
        match_method="llm",
        confidence=0.87,
        notes="multi exploded",
    )
    restored = QuestionVariableLink.from_json(link.to_json())
    assert restored == link


def test_numeric_comparison_reads_a_number_stored_as_text() -> None:
    """A CSV column of "0".."100" mixed with "Refused" imports as text.

    `"25" > 0` raised TypeError — caught and read as "condition not satisfied",
    so every respondent failed a `[IF I2 > 0]` gate and the routing check
    reported a 100% violation that was entirely ours.
    """
    from src.core.survey_definition import Expression, evaluate

    gt_zero = Expression(op="gt", qid="I2", value=0)
    assert evaluate(gt_zero, {"I2": "25"}) is True
    assert evaluate(gt_zero, {"I2": "0"}) is False
    assert evaluate(gt_zero, {"I2": 25}) is True
    # Not a number at all: the condition is simply not satisfied.
    assert evaluate(gt_zero, {"I2": "Refused"}) is False


def test_numeric_comparison_does_not_fall_back_to_string_order() -> None:
    """On string order "100" < "25" — a silently wrong answer, not an error."""
    from src.core.survey_definition import Expression, evaluate

    assert evaluate(Expression(op="lt", qid="Y", value=25), {"Y": "100"}) is False
    assert evaluate(Expression(op="gt", qid="Y", value=25), {"Y": "100"}) is True


def test_a_condition_on_a_grid_without_a_row_reads_every_row():
    """A scripter writes "ASK IF Q6=1" against a brand-usage grid meaning "any
    row". The grid's {row: column} answer used to reach a set() and raise
    TypeError: unhashable type: 'dict' — which a respondent met as a server
    error mid-interview, and which stopped the pre-link check running at all."""
    answers = {"Q6": {"1": 2, "2": 5}}
    assert evaluate(Expression(op="in", qid="Q6", values=[5, 9]), answers) is True
    assert evaluate(Expression(op="in", qid="Q6", values=[7, 9]), answers) is False
    assert evaluate(Expression(op="selected", qid="Q6", value=2), answers) is True
    assert evaluate(Expression(op="selected", qid="Q6", value=3), answers) is False


def test_a_multi_answer_grid_row_is_read_the_same_way():
    """A multi-select grid stores a LIST per row; those codes count too."""
    answers = {"Q7": {"1": [3, 4], "2": [9]}}
    assert evaluate(Expression(op="selected", qid="Q7", value=4), answers) is True
    assert evaluate(Expression(op="selected", qid="Q7", value=5), answers) is False


def test_no_answer_shape_can_raise_out_of_a_routing_comparison():
    """Whatever ends up stored, routing returns a verdict rather than a 500."""
    for stored in [{"a": {"nested": 1}}, [[1, 2]], [{"x": 1}], {"1": None}]:
        assert evaluate(Expression(op="selected", qid="Q1", value=1), {"Q1": stored}) in (
            True,
            False,
        )


def test_a_mask_against_a_grid_question_survives_the_grid_answer():
    """The second half of the same production bug: the MASK path did its own
    `{chosen}` and raised "unhashable type: 'dict'", which a respondent met as
    a server error at question 27 of 68.

    Not crashing was only half of it. The first fix made this return the
    COLUMN codes — {1, 2} for "Yes to CVS, No to Walgreens" — which is the
    answer to a different question and masks the list against numbers that
    mean nothing to it. It returns the ROWS the respondent picked now, which
    is what "only the brands they have heard of" has always meant."""
    from src.engines.fielding.dryrun import _mask_allowed_codes

    aware = Question(
        qid="Q5", text="Which pharmacies have you heard of?", type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        cols=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    ranked = Question(
        qid="Q27", text="Rank your top 3", type=QuestionType.RANK,
        options=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q5"}]}},
    )
    by_qid = {"Q5": aware, "Q27": ranked}

    assert _mask_allowed_codes(ranked, {"Q5": {"1": 1, "2": 2}}, by_qid) == {1}  # CVS only
    assert _mask_allowed_codes(ranked, {"Q5": {"1": 1, "2": 1}}, by_qid) == {1, 2}
    assert _mask_allowed_codes(ranked, {"Q5": [1]}, by_qid) == {1}
    assert _mask_allowed_codes(ranked, {"Q5": 2}, by_qid) == {2}


def test_routing_and_masks_read_a_grid_as_the_two_different_questions_they_ask():
    """These deliberately differ, and an earlier version of this test asserted
    they should not — wrongly.

    Routing asks what was ANSWERED: "ASK IF Q5=1" against a usage grid means
    some row was answered 1, so the codes present are what matters. A mask
    asks which ROWS were picked: "only the brands they know" is about CVS and
    Walgreens, not about the Yes column. Same data, two questions; one shared
    reading gets one of them wrong."""
    from src.engines.questionnaire.grid_masks import mask_codes

    grid = Question(
        qid="Q5", text="heard of?", type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        cols=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    answer = {"1": 1, "2": 2}  # Yes to CVS, No to Walgreens
    assert selected_codes(answer) == {1, 2}      # routing: these codes were answered
    assert mask_codes(grid, answer) == {1}       # masking: this row was picked
