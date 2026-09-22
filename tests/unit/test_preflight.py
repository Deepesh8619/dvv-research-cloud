"""The check a whole questionnaire gets the moment before a link exists."""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.preflight import preflight
from src.engines.questionnaire import schema

YES_NO = [Option(code=1, label="Yes"), Option(code=2, label="No")]


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def _by_id(result: dict) -> dict:
    return {c["id"]: c for c in result["checks"]}


def test_a_clean_questionnaire_passes_every_check():
    survey = _survey(
        Question(qid="S1", text="Do you buy skincare?", options=YES_NO,
                 terminate_if=Expression(op="eq", qid="S1", value=2)),
        Question(qid="Q1", text="Which do you prefer?", options=YES_NO),
    )
    result = preflight(survey, approved=True)
    assert result["ok"] is True
    assert result["n_warn"] == 0, [c for c in result["checks"] if c["status"] != "pass"]


def test_a_question_with_nothing_to_pick_fails_and_is_named():
    result = preflight(_survey(Question(qid="Q9", text="Rate each brand",
                                        type=QuestionType.GRID_SINGLE)), approved=True)
    answers = _by_id(result)["answers"]
    assert (result["ok"], answers["status"], answers["qids"]) == (False, "fail", ["Q9"])


def test_an_unapproved_questionnaire_fails():
    result = preflight(_survey(Question(qid="Q1", text="?", options=YES_NO)), approved=False)
    assert _by_id(result)["approved"]["status"] == "fail"


def test_a_flag_is_a_failure_until_someone_signs_it_off():
    q = Question(qid="Q1", text="?", options=YES_NO, flags=["AMBIGUOUS_ROUTING"])
    blocking = frozenset({"AMBIGUOUS_ROUTING"})
    assert _by_id(preflight(_survey(q), True, blocking))["flags"]["status"] == "fail"
    signed = preflight(_survey(q), True, blocking, approved_qids={"Q1"})
    assert _by_id(signed)["flags"]["status"] == "warn"


def test_a_guess_the_system_made_is_a_warning_not_a_refusal():
    q = Question(qid="Q8", text="Rate each brand", options=YES_NO,
                 flags=[schema.FLAG_LIST_INHERITED])
    result = preflight(_survey(q), approved=True)
    assert result["ok"] is True
    assert _by_id(result)["guesses"]["qids"] == ["Q8"]


def test_a_maxdiff_block_refuses_the_link_and_says_what_it_is():
    survey = _survey(
        Question(qid="Q1", text="Do you buy skincare?", options=YES_NO),
        Question(qid="Q20", text="Which is most important?",
                 source_text="[MAXDIFF EXERCISE — 12 SCREENS, 4 ITEMS PER SCREEN]",
                 options=YES_NO),
    )
    result = preflight(survey, approved=True)
    check = _by_id(result)["trade_off"]
    assert check["status"] == "fail"
    assert check["qids"] == ["Q20"]
    # Named, not just refused: "there is a problem with Q20" sends a reviewer
    # looking for a typo.
    assert "MaxDiff" in check["detail"]
    assert not result["ok"]


def test_a_reviewer_who_approves_the_exercise_may_still_open_the_link():
    """Cleared by a person deciding to script the block elsewhere."""
    survey = _survey(
        Question(qid="Q20", text="Which is most important?",
                 source_text="[CONJOINT — 10 TASKS]", options=YES_NO),
    )
    result = preflight(survey, approved=True, approved_qids={"Q20"})
    assert _by_id(result)["trade_off"]["status"] == "pass"


def test_a_trade_off_is_not_also_reported_as_a_routing_flag():
    """One question, one sentence — and the right sentence.

    The generic blocking-flag check says "confirm the flagged routing", which
    is not what is wrong with a MaxDiff block and would send a reviewer to
    look at a condition that is fine.
    """
    survey = _survey(
        Question(qid="Q20", text="Which is most important?",
                 source_text="[MAXDIFF EXERCISE]", options=YES_NO,
                 flags=[schema.FLAG_TRADE_OFF_UNSUPPORTED]),
    )
    result = preflight(survey, approved=True,
                       blocking_flags=frozenset({schema.FLAG_TRADE_OFF_UNSUPPORTED}))
    checks = _by_id(result)
    assert checks["flags"]["qids"] == []
    assert checks["trade_off"]["qids"] == ["Q20"]
