"""A condition that cannot fire must not look like a condition nobody breached.

Run the data check against a dead screen-out and it reports zero violations —
identical to a clean result. That is the same shape of bug as a terminate whose
codes were written to the wrong field, where 16 of 17 screen-outs silently
matched nobody. These conditions are therefore audited against the data map
BEFORE any respondent row is read.
"""

from __future__ import annotations

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.qc.condition_audit import audit_conditions


def _datamap(rows):
    dm = DataMap()
    for name, values in rows:
        dm.add(Variable(name=name, label=name, type=VarType.SINGLE, values=values))
    return dm


def _survey(questions):
    return SurveyDefinition(
        study_id="s", title="t", source="extracted", status="draft",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def _q(qid, **kw):
    return Question(
        qid=qid, text=qid, type=QuestionType.SINGLE,
        options=[Option(code=1, label="A"), Option(code=2, label="B")], **kw
    )


def test_a_screen_out_on_scripted_codes_passes():
    survey = _survey([_q("S1", terminate_if=Expression.from_dict(
        {"op": "in", "qid": "S1", "values": [2]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="S1", variable_names=["S1"])],
        _datamap([("S1", {1: "Yes", 2: "No"})]),
    )
    assert audit.issues == []
    assert audit.passed
    assert audit.n_checked == 1  # it did look


def test_a_screen_out_on_a_code_that_was_never_scripted_is_an_error():
    """The silent failure: this fires for nobody and the data check says zero."""
    survey = _survey([_q("S1", terminate_if=Expression.from_dict(
        {"op": "in", "qid": "S1", "values": [5, 6]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="S1", variable_names=["S1"])],
        _datamap([("S1", {1: "Yes", 2: "No"})]),
    )
    issue = next(i for i in audit.issues if i.kind == "unscriptable_code")
    assert issue.severity == "error"
    assert "5, 6" in issue.detail or "[5, 6]" in issue.detail
    assert audit.passed is False


def test_a_partly_scriptable_condition_is_only_a_warning():
    """Code 2 exists, code 9 does not — the condition still fires for some."""
    survey = _survey([_q("S1", terminate_if=Expression.from_dict(
        {"op": "in", "qid": "S1", "values": [2, 9]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="S1", variable_names=["S1"])],
        _datamap([("S1", {1: "Yes", 2: "No"})]),
    )
    issue = next(i for i in audit.issues if i.kind == "unscriptable_code")
    assert issue.severity == "warning"
    assert audit.passed is True


def test_a_condition_naming_a_variable_that_does_not_exist():
    survey = _survey([_q("Q5", show_if=Expression.from_dict(
        {"op": "in", "qid": "QGHOST", "values": [1]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="Q5", variable_names=["Q5"])],
        _datamap([("Q5", {1: "A"})]),
    )
    assert [i.kind for i in audit.issues] == ["unknown_variable"]


def test_an_empty_code_list_matches_nobody():
    survey = _survey([_q("S1", terminate_if=Expression.from_dict(
        {"op": "in", "qid": "S1", "values": []}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="S1", variable_names=["S1"])],
        _datamap([("S1", {1: "Yes", 2: "No"})]),
    )
    assert [i.kind for i in audit.issues] == ["no_codes"]


def test_a_multi_punch_carries_its_own_code():
    """`SS_RESPr12` IS punch 12 — the codes are in the column names, not in a
    value list, and a condition on code 12 must not be called unscriptable."""
    survey = _survey([_q("SS_RESP", terminate_if=Expression.from_dict(
        {"op": "in", "qid": "SS_RESP", "values": [12]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="SS_RESP", variable_names=["SS_RESPr1", "SS_RESPr12"])],
        _datamap([("SS_RESPr1", {0: "Unchecked", 1: "Checked"}),
                  ("SS_RESPr12", {0: "Unchecked", 1: "Checked"})]),
    )
    assert [i for i in audit.issues if i.kind == "unscriptable_code"] == []


def test_an_open_response_is_not_code_audited():
    """Testing a code against an open number is meaningless, not wrong — the
    type mismatch is already reported once by the data check."""
    survey = _survey([_q("AGE", terminate_if=Expression.from_dict(
        {"op": "lt", "qid": "AGE", "value": 18}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="AGE", variable_names=["AGE"])],
        _datamap([("AGE", {})]),
    )
    assert audit.issues == []


def test_a_nested_condition_is_audited_leaf_by_leaf():
    """A negated screen-out wraps its real test in `not`."""
    survey = _survey([_q("S1", terminate_if=Expression.from_dict(
        {"op": "not", "children": [{"op": "in", "qid": "S1", "values": [7]}]}))])
    audit = audit_conditions(
        survey,
        [QuestionVariableLink(qid="S1", variable_names=["S1"])],
        _datamap([("S1", {1: "Yes", 2: "No"})]),
    )
    assert [i.kind for i in audit.issues] == ["unscriptable_code"]


def test_a_mask_from_an_unscripted_source_is_flagged():
    masked = _q("Q18a", validation={"techniques": {
        "masks": [{"source_qid": "Q18", "mode": "selected", "exclude_qid": None}],
        "pipes": [], "least_fill": False}})
    audit = audit_conditions(
        _survey([masked]),
        [QuestionVariableLink(qid="Q18a", variable_names=["Q18a"])],
        _datamap([("Q18a", {1: "A"})]),
    )
    assert [i.kind for i in audit.issues] == ["unknown_variable"]
    assert audit.issues[0].where == "mask"
