"""A hidden variable's column can arrive empty, and an empty column looks like data.

Three kinds of derived variable reach the data file and only two carry a value.
The third gets its column — the banner is cut by it, so a missing column is
worse — but its derivation is prose the document wrote for a human scripter.
"""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.materialize import uncomputed_derived
from src.engines.questionnaire import schema

_BANDS = [Option(code=1, label="18-34"), Option(code=2, label="35+")]


def _survey(*questions):
    return SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="b", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def test_a_coded_hidden_variable_is_reported_as_unfilled():
    """"CODE AGE - BASED ON RESPONSES AT S1" with its bands underneath.

    It becomes a column because every banner cut by age band reads it, and
    nothing computes the value — so the analyst cuts by age band, gets a table
    of blanks, and reads it as a fielding problem.
    """
    coded = Question(
        qid="C1", text="CODE AGE - BASED ON RESPONSES AT S1",
        type=QuestionType.INFO, options=_BANDS,
        flags=[schema.FLAG_DERIVED_VARIABLE],
    )
    assert uncomputed_derived(_survey(coded)) == ["C1"]


def test_a_hidden_variable_with_a_readable_base_is_not_reported():
    """It is computed per respondent from the condition we parsed."""
    in_path = Question(
        qid="HID_PATH", text="In path", type=QuestionType.INFO, options=_BANDS,
        flags=[schema.FLAG_DERIVED_VARIABLE],
        show_if=Expression.from_dict({"op": "eq", "qid": "S1", "value": 1}),
    )
    assert uncomputed_derived(_survey(in_path)) == []


def test_a_hidden_variable_with_no_column_at_all_is_not_reported():
    """No options and no base means materialize emits nothing for it. There is
    no empty column to warn about — the review UI already says it needs setting
    up, and reporting it here would be the same thing twice."""
    bare = Question(
        qid="HID_TOPBENEFIT", text="HIDDEN TOP BENEFIT", type=QuestionType.INFO,
        flags=[schema.FLAG_DERIVED_VARIABLE],
    )
    assert uncomputed_derived(_survey(bare)) == []


def test_ordinary_questions_are_never_reported():
    asked = Question(qid="S1", text="Age?", type=QuestionType.SINGLE, options=_BANDS)
    display = Question(qid="INTRO", text="Welcome", type=QuestionType.INFO)
    assert uncomputed_derived(_survey(asked, display)) == []


def test_a_prose_derived_variable_carries_the_flag_not_only_the_type():
    """The prose parser recognised these and typed them `info`, and the FLAG
    was dropped on the floor.

    Everything downstream keys on the flag, not the type: materialize decides
    whether to emit a column by it, the review UI says "needs manual setup" by
    it, the buckets group by it. So detection worked and nothing acted on it —
    on the Bubble document that is ten hidden variables typed correctly and
    labelled nowhere, and on Vivo four more.
    """
    from src.engines.questionnaire.extractor import extract_question
    from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

    blocks = [
        DocumentBlock("paragraph", "S0. CODE SAMPLE SOURCE", 0),
        DocumentBlock("paragraph", "1. Consumer panel", 1),
        DocumentBlock("paragraph", "2. Client CRM", 2),
    ]
    question = extract_question(
        segment_into_questions(blocks)[0], None, None, None, None
    ).question
    assert question.type is QuestionType.INFO
    assert schema.FLAG_DERIVED_VARIABLE in question.flags


def test_a_coded_hidden_variable_gets_its_column_once_it_is_flagged():
    """The column is emitted on the flag. Without it the variable the banner is
    cut by was simply absent from the delivery file."""
    from datetime import datetime

    from src.engines.fielding.materialize import STATUS_COMPLETE, materialize_fielding

    class _Session:
        def __init__(self, sid):
            self.id = sid
            self.status_code = STATUS_COMPLETE
            self.started_at = datetime(2026, 1, 1).isoformat()
            self.completed_at = self.started_at
            self.last_seen_at = None
            self.current_page = 1
            self.entry_params: dict = {}
            self.pid = ""
            self.block_order = None
            self.maxdiff_version = None

    asked = Question(qid="S1", text="Age?", type=QuestionType.SINGLE, options=_BANDS)
    coded = Question(
        qid="C1", text="CODE AGE - BASED ON S1", type=QuestionType.INFO,
        options=_BANDS, flags=[schema.FLAG_DERIVED_VARIABLE],
    )
    datamap, _df = materialize_fielding(
        _survey(asked, coded), [_Session("r1")], {"r1": {"S1": 1}}
    )
    assert "C1" in [v.name for v in datamap.variables]
