"""Coded hidden variables: a rule proposed from the document, approved once, run live.

Bubble D2a "CODE SIZE OF HOUSEHOLD" (1 / 2 / 3 / 4 or more) was a data column
delivered empty for every respondent.
"""

from types import SimpleNamespace

from src.ai.router import AIResponse
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.decisions import terminating_question
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding, uncomputed_derived
from src.engines.questionnaire import second_look as sl
from src.engines.questionnaire.derived import derived_values, rule_from_document
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _d2() -> Question:
    return Question(qid="D2", type=QuestionType.NUMERIC, text="How many people live in your home?")


def _d2a() -> Question:
    return Question(qid="D2a", type=QuestionType.INFO, text="CODE SIZE OF HOUSEHOLD",
                    flags=["DERIVED_VARIABLE"],
                    options=[Option(1, "1"), Option(2, "2"), Option(3, "3"),
                             Option(4, "4 or more")])


def _survey(*qs: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="s", questions=list(qs),
                            blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])])


def _approved(survey: SurveyDefinition, qid: str) -> None:
    q = next(x for x in survey.questions if x.qid == qid)
    after, _how = rule_from_document(q, survey)
    q.options = after.options
    q.validation = after.validation


def test_household_bands_are_read_over_the_question_they_are_coded_from():
    s = _survey(_d2(), _d2a())
    _approved(s, "D2a")
    assert derived_values(s, {"D2": 1}) == {"D2a": 1}
    assert derived_values(s, {"D2": 6}) == {"D2a": 4}
    assert derived_values(s, {}) == {}  # not answered yet: unset, not guessed


def test_a_definition_column_and_a_condition_in_the_label_are_rules_too():
    emp = Question(qid="SS_EMP", type=QuestionType.SINGLE, text="Employees",
                   options=[Option(c, str(c)) for c in range(1, 10)])
    size = Question(qid="HID_COMP_SIZE", type=QuestionType.INFO, text="Company size",
                    options=[Option(1, "Micro", definition="SS_EMP = 2"),
                             Option(2, "Small", definition="SS_EMP= 3-4")])
    s11 = Question(qid="S11", type=QuestionType.SINGLE, text="Parent?",
                   options=[Option(1, "Yes"), Option(3, "No")])
    parent = Question(qid="S11a", type=QuestionType.INFO, text="CODE PARENT",
                      flags=["DERIVED_VARIABLE"],
                      options=[Option(1, "Parent (S11=1)"), Option(2, "All other (S11=3)")])
    s = _survey(emp, size, s11, parent)
    _approved(s, "HID_COMP_SIZE")
    _approved(s, "S11a")
    assert derived_values(s, {"SS_EMP": 4, "S11": 3}) == {"HID_COMP_SIZE": 2, "S11a": 2}


def test_codes_over_different_grid_rows_can_all_hold():
    usage = Question(qid="DA_USAGE", type=QuestionType.GRID_SINGLE, text="Use",
                     rows=[Option(1, "Google"), Option(5, "Meta")],
                     cols=[Option(c, str(c)) for c in range(1, 6)])
    aware = Question(qid="HID_AWARE", type=QuestionType.INFO, text="Aware",
                     options=[Option(1, "Google aware", definition="DA_USAGE 1 = 3-5"),
                              Option(2, "Meta aware", definition="DA_USAGE 5 = 3-5")])
    s = _survey(usage, aware)
    _approved(s, "HID_AWARE")
    assert derived_values(s, {"DA_USAGE": {"1": 4, "5": 5}}) == {"HID_AWARE": [1, 2]}


def test_the_second_look_proposes_and_never_applies_a_rule():
    d2a = _d2a()
    sl.second_look(_survey(_d2(), d2a), use_model=False)
    r = d2a.validation["resolution"]
    assert r["status"] == "ready" and "D2" in r["summary"]
    assert all(o.derive_if is None for o in d2a.options)  # waits for a person
    assert r["proposed"]["options"][3]["derive_if"] is not None


def test_a_value_from_outside_the_answers_is_said_to_be_so():
    geo = Question(qid="HID_GEO", type=QuestionType.INFO, text="Division based on ZIP",
                   options=[Option(1, "New England", definition="Based on C_ZIP")])
    sl.second_look(_survey(geo), use_model=False)
    assert geo.validation["resolution"]["status"] == "explained"


def test_the_model_writes_conditions_in_the_document_grammar(monkeypatch):
    q2c = Question(qid="Q2c", type=QuestionType.SINGLE, text="When did you last buy?",
                   options=[Option(c, str(c)) for c in range(1, 6)])
    audience = Question(qid="C10", type=QuestionType.INFO, text="AUDIENCE CODE",
                        flags=["DERIVED_VARIABLE"],
                        options=[Option(1, "Recent buyer"), Option(2, "Lapsed")])

    def fake(task, prompt, schema, **_):
        assert "Q2c" in prompt
        return AIResponse({"codes": [{"code": 1, "condition": "Q2c = 1,2"},
                                     {"code": 2, "condition": "Q2c = 3-5"}]}, "bedrock", "qwen3")

    monkeypatch.setattr("src.ai.router.run_task", fake)
    sl.second_look(_survey(q2c, audience), use_model=True)
    r = audience.validation["resolution"]
    assert r["status"] == "ready" and r["source"].startswith("ai:")


def test_once_approved_it_screens_out_routes_and_reaches_the_data():
    s = _survey(_d2(), _d2a())
    _approved(s, "D2a")
    gate = Question(qid="Q9", type=QuestionType.SINGLE, text="x", options=[Option(1, "a")],
                    terminate_if=Expression(op="eq", qid="D2a", value=4))
    s.questions.append(gate)
    s.blocks[0].question_ids.append("Q9")
    assert terminating_question(s, {"D2": 7}).qid == "Q9"
    assert terminating_question(s, {"D2": 2}) is None
    assert "D2a" not in uncomputed_derived(s)

    compiled = compile_to_surveyjs(s)
    calc = {c["name"]: c["expression"] for c in compiled["calculatedValues"]}
    assert calc["D2a"].startswith("iif(")

    sessions = [SimpleNamespace(id=i, status_code=10, started_at=None, completed_at=None,
                                entry_params={}, block_order=None) for i in ("a", "b")]
    _dm, frame = materialize_fielding(s, sessions, {"a": {"D2": 1}, "b": {"D2": 5}}, ALL_STATUSES)
    assert list(frame["D2a"]) == [1, 4]
