"""Survey instrument model: construction, JSON round-trip, validation.

Fixtures build a small but realistic instrument — a screener block with a
terminate condition and a main block with a grid question — then deliberately
break it to prove each validation rule fires.
"""

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
