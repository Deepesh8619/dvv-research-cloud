"""Rebuild the questions extraction missed, using the script's own record.

The data map does not guess: it holds every variable name, the question text,
and every option label with its punch code. On a real client study it described
113 questions while extraction had produced 24. A question it describes and the
document parser missed does not need guessing twice.
"""

from __future__ import annotations

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.datamap_recovery import (
    FLAG_RECOVERED,
    questions_from_datamap,
    reconcile,
    recover_missing,
)


def _datamap():
    dm = DataMap()
    # A single-answer question.
    dm.add(Variable(name="SS_EMP", label="What is your work status?",
                    type=VarType.SINGLE, values={1: "Employed", 2: "Not employed"}))
    # A multi-select, one column per punch, parent text on each child.
    for code, label in ((1, "Generate leads"), (2, "Website visits"), (98, "Other, specify:")):
        dm.add(Variable(name=f"DA_GOALSr{code}", label=label, type=VarType.SINGLE,
                        values={0: "Unchecked", 1: "Checked"},
                        question_ref="DA_GOALS",
                        question_text="Which objectives do you seek?"))
    # Things that were never asked.
    dm.add(Variable(name="HID_SEGMENT", label="Hidden: segment", type=VarType.SINGLE))
    dm.add(Variable(name="record", label="Record number", type=VarType.NUMERIC))
    dm.add(Variable(name="DA_GOALSr98oe", label="Other, specify", type=VarType.TEXT))
    return dm


def _survey(questions):
    return SurveyDefinition(
        study_id="s", title="t", source="extracted", status="draft",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def test_only_answerable_columns_become_questions():
    """Hidden variables, system columns and other-specify text were never asked."""
    found = {q.qid for q in questions_from_datamap(_datamap())}
    assert found == {"SS_EMP", "DA_GOALS"}


def test_a_multi_is_one_question_over_many_punch_columns():
    question = next(q for q in questions_from_datamap(_datamap()) if q.qid == "DA_GOALS")
    assert question.is_multi
    assert len(question.variables) == 3
    assert question.text == "Which objectives do you seek?"


def test_reconcile_reports_both_directions():
    survey = _survey([Question(qid="SS_EMP", text="Work status?", type=QuestionType.SINGLE),
                      Question(qid="INTRO", text="Welcome", type=QuestionType.INFO)])
    result = reconcile(survey, _datamap())
    assert result.summary() == {
        "in_both": 1, "only_in_datamap": 1, "only_in_questionnaire": 1
    }
    assert [q.qid for q in result.missing_from_questionnaire] == ["DA_GOALS"]
    assert result.missing_from_datamap == ["INTRO"]


def test_a_recovered_multi_keeps_the_real_punch_codes():
    """`DA_GOALSr98` is punch 98, not the third option. Renumbering it would
    make every later data check compare against the wrong codes."""
    questions, _ = recover_missing(_survey([]), _datamap())
    goals = next(q for q in questions if q.qid == "DA_GOALS")
    assert goals.type is QuestionType.MULTI
    assert [o.code for o in goals.options] == [1, 2, 98]
    assert goals.options[2].label == "Other, specify:"


def test_a_recovered_single_takes_its_options_from_the_value_labels():
    questions, _ = recover_missing(_survey([]), _datamap())
    emp = next(q for q in questions if q.qid == "SS_EMP")
    assert emp.type is QuestionType.SINGLE
    assert [(o.code, o.label) for o in emp.options] == [(1, "Employed"), (2, "Not employed")]


def test_a_recovered_question_is_flagged_never_silently_merged():
    """Its wording comes from the script, not the client's document — the two
    can legitimately differ, and hiding that would hide a real disagreement."""
    questions, _ = recover_missing(_survey([]), _datamap())
    for question in questions:
        assert FLAG_RECOVERED in question.flags
        assert question.extraction_method == "datamap"


def test_a_recovered_question_never_invents_routing():
    """The data map records what was COLLECTED, not who was asked."""
    questions, _ = recover_missing(_survey([]), _datamap())
    for question in questions:
        assert question.show_if is None
        assert question.terminate_if is None
        assert "routing" in (question.validation or {}).get("missing", [])


def test_links_come_back_exact_not_re_guessed():
    """A recovered question was built FROM named columns, so its link is
    already known; sending it back through the fuzzy matcher would make a
    multi re-guess the very columns it came from."""
    _, links = recover_missing(_survey([]), _datamap())
    by_qid = {link.qid: link for link in links}
    assert by_qid["DA_GOALS"].variable_names == [
        "DA_GOALSr1", "DA_GOALSr2", "DA_GOALSr98"
    ]
    assert by_qid["DA_GOALS"].confidence == 1.0
    assert by_qid["DA_GOALS"].match_method == "exact"


def test_nothing_is_recovered_when_the_questionnaire_is_already_complete():
    survey = _survey([
        Question(qid="SS_EMP", text="Work status?", type=QuestionType.SINGLE),
        Question(qid="DA_GOALS", text="Which objectives?", type=QuestionType.MULTI,
                 options=[Option(code=1, label="Generate leads")]),
    ])
    questions, links = recover_missing(survey, _datamap())
    assert questions == []
    assert links == []


def test_matching_ignores_case_and_underscores():
    """`ss_emp` and `SS_EMP` are the same question; recovering it twice would
    put a duplicate qid into the survey."""
    survey = _survey([Question(qid="ss_emp", text="Work status?", type=QuestionType.SINGLE)])
    questions, _ = recover_missing(survey, _datamap())
    assert [q.qid for q in questions] == ["DA_GOALS"]
