"""The audit must name a stale definition — and stay quiet on a clean one."""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.definition_audit import (
    audit_all,
    audit_definition,
    format_report,
)


def _survey(*questions: Question, metadata: dict | None = None) -> SurveyDefinition:
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )
    survey.metadata.update(metadata or {})
    return survey


def _checks(survey) -> set[str]:
    return {f.check for f in audit_definition(survey)}


def test_a_clean_definition_reports_nothing():
    survey = _survey(
        Question(
            qid="Q1",
            text="Favourite colour?",
            options=[Option(code=1, label="Red"), Option(code=2, label="Blue")],
        )
    )
    assert audit_definition(survey) == []
    assert "0 affected" in format_report(audit_all([]))


def test_prose_stored_as_an_option_is_caught():
    survey = _survey(
        Question(
            qid="P1",
            text="Are you a parent?",
            options=[
                Option(code=1, label="Yes"),
                Option(code=2, label="No"),
                Option(
                    code=3,
                    label=(
                        "In this survey, we're looking to interview young women ages 13 "
                        "to 17 about their skincare habits, and we would like your "
                        "permission to speak with your teenager."
                    ),
                ),
            ],
        )
    )
    assert "prose_as_option" in _checks(survey)


def test_a_scripter_tag_left_in_a_label_is_caught():
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            type=QuestionType.MULTI,
            options=[
                Option(code=1, label="Ulta"),
                Option(code=2, label="None of the above [ANCHOR] [EXCLUSIVE]"),
            ],
        )
    )
    assert "scripter_tag_in_label" in _checks(survey)


def test_an_unapplied_recruitment_path_is_caught():
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            options=[Option(code=1, label="Ulta")],
            source_text="S6. Which retailers?\nParent Path (IF S0=1 AND S1=26-60) ASK P1-P3",
        ),
        Question(qid="P1", text="Parent?", options=[Option(code=1, label="Yes")]),
    )
    assert "section_path_unapplied" in _checks(survey)


def test_a_path_the_current_parser_applied_is_not_reported():
    """Same header, but `section_paths` metadata proves this pass already ran."""
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            options=[Option(code=1, label="Ulta")],
            source_text="S6. Which retailers?\nParent Path (IF S0=1 AND S1=26-60) ASK P1-P3",
        ),
        Question(
            qid="P1",
            text="Parent?",
            options=[Option(code=1, label="Yes")],
            show_if=Expression(op="in", qid="S0", values=[1]),
        ),
        metadata={"section_paths": [{"path": "Parent Path", "questions": ["P1"]}]},
    )
    assert "section_path_unapplied" not in _checks(survey)


def test_exclusive_on_a_single_select_is_caught():
    survey = _survey(
        Question(
            qid="Q4",
            text="Which brand?",
            type=QuestionType.SINGLE,
            options=[
                Option(code=1, label="Ulta"),
                Option(code=2, label="None", exclusive=True),
            ],
        )
    )
    assert "exclusive_on_single" in _checks(survey)


def test_a_self_referential_base_is_caught():
    survey = _survey(
        Question(
            qid="P1",
            text="Parent?",
            options=[Option(code=1, label="Yes")],
            show_if=Expression(op="in", qid="P1", values=[1]),
        )
    )
    assert "self_referential_base" in _checks(survey)


def test_a_grid_with_no_rows_is_caught():
    """It emits NO data columns — and used to pass validation on the strength of
    `options`, which a grid cannot use. One real question was in exactly this
    state: typed correctly, 12 options, delivering nothing."""
    from src.engines.questionnaire import schema
    from src.engines.questionnaire.extractor import ExtractionResult, _validate_question

    q = Question(
        qid="Q6",
        text="How often do you buy each category?",
        type=QuestionType.GRID_SINGLE,
        options=[Option(code=i, label=f"Category {i}") for i in range(1, 13)],
    )
    result = ExtractionResult(question=q, confidence={})
    _validate_question(result, {})
    assert schema.FLAG_EMPTY_OPTIONS in result.flags

    q.rows = [Option(code=1, label="Cleansers")]
    q.cols = [Option(code=1, label="Often"), Option(code=2, label="Never")]
    ok = ExtractionResult(question=q, confidence={})
    _validate_question(ok, {})
    assert schema.FLAG_EMPTY_OPTIONS not in ok.flags
