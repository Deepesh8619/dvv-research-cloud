"""Refuse, warn, ask again: made-up wording of the shape the notes use."""

from src.ai.router import BackendUnavailable
from src.core.survey_definition import Expression, evaluate
from src.engines.fielding.decisions import terminating_question
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire import note_ledger
from src.engines.questionnaire.extractor import extract_survey_definition
from src.engines.questionnaire.logic_proof import prove
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs

_ENTRY = ("[PN: Allow a write-in whole number or check 'Prefer not to answer' (which cannot be "
          "combined with any other response). Acceptable range: 1-120. If less than 18, "
          "terminate.]")
_FLOW = ("[PN: If respondent selects ‘Prefer not to answer,’ show exact age refusal: Sorry! "
         "We need your exact age for you to continue. If you select ‘Prefer not to answer’ "
         "then the survey will close. If respondent selects ‘Prefer not to answer’ a second "
         "time, terminate.]")


def _survey(monkeypatch, lines):
    def off(*a, **k):
        raise BackendUnavailable("AI off")

    monkeypatch.setattr(ex, "run_task", off)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(lines, 1)]
    return ex.extract_survey_definition(segment_into_questions(blocks), "t")


def test_second_ask_is_a_real_question(monkeypatch):
    survey = _survey(monkeypatch, ["S1. How old are you?", _ENTRY, _FLOW,
                                   "S2. Which region do you live in?"])
    s1, again, s2 = survey.questions
    assert (s1.qid, again.qid, s2.qid) == ("S1", "S1_REASK", "S2")
    opt = s1.options[0].code
    assert again.show_if.to_dict()["values"] == [opt] and again.show_if.qid == "S1"
    assert again.text.startswith("Sorry! We need your exact age")
    assert again.validation["min"] == 1 and again.validation["max"] == 120
    assert again.extraction_method == "note_flow" and "second" in again.extraction_evidence["flow"]
    assert survey.metadata["note_flows"]["reask"][0]["replaces"] == "S1"
    assert [qid for b in survey.blocks for qid in b.question_ids] == ["S1", "S1_REASK", "S2"]

    def ends(answers):
        q = terminating_question(survey, answers)
        return q.qid if q else None

    assert ends({"S1": opt}) is None  # the first refusal does not end the survey
    assert ends({"S1": opt, "S1_REASK": opt}) == "S1_REASK"
    assert ends({"S1": opt, "S1_REASK": 17}) == "S1_REASK"
    assert ends({"S1": opt, "S1_REASK": 40}) is None
    assert ends({"S1": 15}) == "S1"
    assert not evaluate(again.show_if, {"S1": 40})
    assert prove(survey)["unreachable"] == []
    compile_to_surveyjs(survey)
    records = note_ledger.read_notes(survey)
    assert {r["outcome"] for r in records if r["kind"] == "reask"} == {"applied"}
    assert not [r for r in records if r["outcome"] == "unread" and r["qid"] == "S1"]


def test_an_inexact_flow_adds_nothing_and_is_asked(monkeypatch):
    vague = "[PN: If respondent selects ‘Prefer not to answer’ a second time, terminate.]"
    survey = _survey(monkeypatch, ["S1. How old are you?", _ENTRY, vague])
    assert [q.qid for q in survey.questions] == ["S1"]
    records = note_ledger.read_notes(survey)
    assert [r["outcome"] for r in records if r["kind"] == "reask"] == ["asked"]


def test_a_twice_tag_on_the_opt_out_does_not_end_the_first_ask():
    for line in ("99. Prefer not to answer TERMINATE (IF SELECTED TWICE)",
                 "99. Prefer not to answer [TERMINATE IF SELECTED A SECOND TIME]"):
        fields = parse_prose_question(
            "S1. Pick one.\n1. Under 18 [TERMINATE]\n2. Adult\n" + line, "S1")
        assert fields.terminate_if.to_dict()["values"] == [1]
        assert fields.options[-1].label == "Prefer not to answer"
        assert len(fields.options) == 3


def test_a_stray_first_ask_terminate_on_the_opt_out_is_removed(monkeypatch):
    survey = _survey(monkeypatch, ["S1. How old are you?", _ENTRY, _FLOW])
    s1 = survey.questions[0]
    code = s1.options[0].code
    assert s1.terminate_if.op == "lt"
    s1.terminate_if = Expression(op="or", children=[
        s1.terminate_if, Expression(op="in", qid="S1", values=[code])])
    survey.questions.pop(1)
    from src.engines.questionnaire import note_flows
    note_flows.apply(survey, [])
    assert survey.questions[0].terminate_if.op == "lt"
    assert survey.questions[1].terminate_if.op == "or"


def test_a_soft_warning_is_kept_not_enforced(monkeypatch):
    note = "[PN: Show warning: Please check this number. but allow respondent to continue]"
    survey = _survey(monkeypatch, ["S1. How old are you?", _ENTRY, note])
    assert survey.questions[0].validation["soft_message"] == "Please check this number"
    kinds = {r["kind"]: r["outcome"] for r in note_ledger.read_notes(survey)}
    assert kinds["warning"] == "scripter"


def test_the_whole_note_reaches_the_link_without_ai(monkeypatch):
    """Read from the document, compiled: the respondent gets both asks and no notes."""
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    for key in ("BEDROCK_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN"):
        monkeypatch.setenv(key, "")
    lines = ["S1. How old are you?",
             "[PN: Allow a whole number or check ‘Skip’ (which cannot be combined with any "
             "other response). Acceptable range: 1-120. If less than 18, terminate.]",
             "[PN: If respondent selects ‘Skip,’ show refusal: Sorry! We need your age. If "
             "respondent selects ‘Skip’ a second time, terminate.]"]
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(lines)]
    survey = extract_survey_definition(segment_into_questions(blocks), "s", blocks=blocks)
    compiled = compile_to_surveyjs(survey)
    by_name = {e["name"]: e for p in compiled["pages"] for e in p["elements"]}
    assert by_name["S1"]["title"] == "How old are you?"
    assert by_name["S1_REASK"]["visibleIf"]
    assert by_name["S1_REASK"]["title"].startswith("Sorry!")
    assert "PN" not in by_name["S1_REASK"]["title"]


def test_a_band_counts_the_second_ask_without_replacing_the_first(monkeypatch):
    from src.engines.questionnaire.reading_questions import analyst_reading

    band = "[PN: Create a variable called AGEBANDS for age ranges: 18-34, 35-49, 50-64, 65+]"
    survey = _survey(monkeypatch, ["S1. How old are you?", _ENTRY, band, _FLOW])
    opt_out = survey.metadata["note_flows"]["reask"][0]["opt_out"]
    var = next(v for v in analyst_reading(survey)["variables"] if v["name"] == "AGEBANDS")
    assert var["status"] == "ready"
    mid = Expression.from_dict(var["bands"][1]["condition"])  # 35-49
    assert mid.referenced_qids() == {"S1", "S1_REASK"}
    assert evaluate(mid, {"S1": 40})                         # typed 40 first time
    assert evaluate(mid, {"S1": opt_out, "S1_REASK": 40})    # typed 40 at the second ask
    assert not evaluate(mid, {"S1": opt_out})                # opted out, never answered
    assert not evaluate(mid, {"S1": 60})                     # outside the band
