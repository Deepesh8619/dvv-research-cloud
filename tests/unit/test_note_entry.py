"""How an answer is entered: the scripter's note settles the type of a question
the reader could not type, on a made-up document with no [NUMERIC] tag."""

from src.ai.router import BackendUnavailable
from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire.note_entry import apply as apply_entry
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs as compile_survey

_NOTE = ("[PN: Allow a write-in whole number or check 'Prefer not to answer' (which cannot be "
         "combined with any other response). Acceptable range: 1-120. If less than 18, terminate.]")


def _survey(q):
    from src.core.survey_definition import Block
    return SurveyDefinition(study_id="s", source="x", status="draft",
                            blocks=[Block(block_id="B", question_ids=[q.qid])], questions=[q])


def test_a_write_in_number_with_an_opt_out_from_the_document(monkeypatch):
    def off(*a, **k):
        raise BackendUnavailable("AI off")

    monkeypatch.setattr(ex, "run_task", off)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(["S1. How old are you?", _NOTE], 1)]
    survey = ex.extract_survey_definition(segment_into_questions(blocks), "t")
    q = survey.questions[0]
    assert q.type is QuestionType.NUMERIC
    assert (q.validation["min"], q.validation["max"]) == (1, 120)
    assert q.terminate_if.to_dict()["value"] == 18
    assert [(o.label, o.exclusive) for o in q.options] == [("Prefer not to answer", True)]
    assert "extraction_error" not in q.validation and "resolution" not in q.validation
    assert not [f for f in q.flags if f.startswith("EXTRACTION_")]
    element = compile_survey(survey)["pages"][0]["elements"][0]
    assert str(q.options[0].code) in element["validators"][0]["expression"]


def test_a_real_single_question_is_never_retyped():
    q = Question(qid="Q1", text="Pick?", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="A"), Option(code=2, label="B")],
                 source_text="[PN: Allow one response.]")
    apply_entry(q, ["Allow one response."], [])
    assert q.type is QuestionType.SINGLE


def test_multiple_responses_need_options_and_min_length_is_kept():
    bare = Question(qid="Q1", text="x", type=QuestionType.INFO,
                    validation={"extraction_error": "x"})
    apply_entry(bare, ["Allow multiple responses."], [])
    assert bare.type is QuestionType.INFO
    oe = Question(qid="Q2", text="why", type=QuestionType.OPEN_TEXT)
    apply_entry(oe, ["OE: 20-character minimum."], [])
    assert oe.validation["min_length"] == 20
    rk = Question(qid="Q3", text="r", type=QuestionType.RANK,
                  options=[Option(code=1, label="A"), Option(code=2, label="B")])
    apply_entry(rk, ["Rank 1,2,3"], [])
    assert rk.validation["rank_top"] == 3
    element = compile_survey(_survey(oe))["pages"][0]["elements"][0]
    assert element["validators"][0]["minLength"] == 20


def test_an_answer_already_listed_is_not_added_twice():
    q = Question(qid="QA", text="Age?", type=QuestionType.NUMERIC,
                 options=[Option(code=99, label="Prefer not to answer TERMINATE (IF SELECTED)")])
    apply_entry(q, ["Allow a whole number or check ‘Prefer not to answer’ (which cannot be combined "
              "with any other response)."], [])
    assert [o.code for o in q.options] == [99]
