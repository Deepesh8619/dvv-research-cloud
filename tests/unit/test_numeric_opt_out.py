"""A number box with a way out: "[Numeric OE]" plus "99: Prefer not to answer"."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.field_report import summarise
from src.engines.fielding.materialize import plan_datamap
from src.renderers.surveyjs_compiler import compile_to_surveyjs

AGE = Question(
    qid="RESP_AGE",
    text="What is your age?",
    type=QuestionType.NUMERIC,
    options=[Option(code=99, label="Prefer not to answer", exclusive=True)],
    validation={"min": 0, "max": 999},
)


def _survey() -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B1", question_ids=["RESP_AGE"])], questions=[AGE],
    )


def test_the_respondent_gets_a_number_box_and_the_way_out():
    [element] = compile_to_surveyjs(_survey())["pages"][0]["elements"]
    assert (element["type"], element["inputType"]) == ("text", "number")
    assert element["optOuts"] == [{"value": 99, "text": "Prefer not to answer"}]
    # The range check lets the opt-out code through.
    [rule] = element["validators"]
    assert rule["expression"] == "{RESP_AGE} = 99 or ({RESP_AGE} >= 0 and {RESP_AGE} <= 999)"


def test_the_data_file_labels_the_code_and_declares_it_missing():
    [variable] = [v for v in plan_datamap(_survey()).variables if v.name == "RESP_AGE"]
    assert variable.values == {99: "Prefer not to answer"}
    assert variable.missing_values == [99]


def test_declining_is_not_averaged_in_as_an_age():
    card = summarise(AGE, [{"RESP_AGE": 30}, {"RESP_AGE": 40}, {"RESP_AGE": 99}])
    assert card["numeric"]["mean"] == 35
    assert card["options"] == [{"code": 99, "label": "Prefer not to answer", "n": 1}]
