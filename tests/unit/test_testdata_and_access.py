"""A dataset before field, and who may take data off the platform.

The dummy data must have the same holes in the same places as the real thing —
that is the whole point of generating it from the survey's own conditions rather
than from a generator written for the purpose.
"""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.access import (
    LinkState,
    may_download_data,
    may_download_datamap,
)
from src.engines.fielding.testdata import synthesise


def _survey() -> SurveyDefinition:
    questions = [
        Question(
            qid="S1",
            text="What is your age?",
            type=QuestionType.NUMERIC,
            terminate_if=Expression(op="lt", qid="S1", value=18),
        ),
        Question(
            qid="S2",
            text="Which do you use? Select all that apply.",
            type=QuestionType.MULTI,
            options=[
                Option(code=1, label="Brand A"),
                Option(code=2, label="Brand B"),
                Option(code=3, label="None of these", exclusive=True, anchored=True),
            ],
        ),
        Question(
            qid="Q1",
            text="How satisfied are you?",
            type=QuestionType.SINGLE,
            options=[Option(code=1, label="Happy"), Option(code=2, label="Not happy")],
            # Only people who named a brand.
            show_if=Expression(op="in", qid="S2", values=[1, 2]),
        ),
    ]
    return SurveyDefinition(
        study_id="T",
        source="authored",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["S1", "S2", "Q1"])],
        questions=questions,
    )


def test_a_dataset_exists_before_a_single_respondent():
    datamap, frame = synthesise(_survey(), n=60, seed=7)
    assert len(frame) == 60
    assert "respondent_id" in frame.columns
    # One column per multi punch, exactly as the live export writes it.
    for column in ("S1", "S2r1", "S2r2", "S2r3", "Q1", "vStatus"):
        assert column in frame.columns, column
    assert {v.name for v in datamap.variables} >= {"S1", "S2r1", "Q1"}


def test_the_dummy_data_has_the_same_holes_as_the_real_thing():
    """A question nobody qualified for is empty here too. If the generator
    ignored the base, QA would be testing a survey no respondent will see."""
    _, frame = synthesise(_survey(), n=120, seed=11)
    named_a_brand = (frame["S2r1"] == 1) | (frame["S2r2"] == 1)
    # Everyone shown Q1 qualified for it...
    assert frame.loc[frame["Q1"].notna(), "Q1"].index.isin(
        frame.loc[named_a_brand].index
    ).all()
    # ...and somebody was excluded, or this test proves nothing.
    assert (~named_a_brand).any()


def test_a_screened_out_respondent_stops_where_the_interview_would():
    _, frame = synthesise(_survey(), n=120, seed=3)
    screened = frame[frame["vStatus"] == 20]
    assert len(screened) > 0
    assert screened["Q1"].isna().all()  # the interview ended at the screen-out
    assert screened["vEndTime"].isna().all()  # and never completed


def test_the_same_seed_gives_the_same_dataset():
    """QA comparing two runs is comparing their code, not their luck."""
    _, first = synthesise(_survey(), n=40, seed=99)
    _, second = synthesise(_survey(), n=40, seed=99)
    assert first.equals(second)


def test_an_unapproved_questionnaire_is_downloadable_by_nobody():
    for role in ("owner", "founder", "analyst", "reviewer"):
        decision = may_download_data(role, questionnaire_approved=False)
        assert decision.allowed is False
        assert "not approved" in decision.reason
    assert may_download_datamap("owner", questionnaire_approved=False).allowed is False


def test_the_people_accountable_for_the_study_may_download_from_approval():
    for role in ("owner", "founder"):
        assert may_download_data(role, questionnaire_approved=True).allowed is True


def test_everyone_else_waits_for_the_field_to_mean_something():
    holding = [LinkState(soft_launch_limit=50, completes=12)]
    decision = may_download_data("analyst", True, links=holding)
    assert decision.allowed is False
    # The refusal says what would change it, not just "no".
    assert "50" in decision.reason and "12" in decision.reason

    reached = [LinkState(soft_launch_limit=50, completes=50)]
    assert may_download_data("analyst", True, links=reached).allowed is True
    assert may_download_data("analyst", True, quota_filled=True).allowed is True


def test_the_column_list_is_not_the_answers():
    """An analyst needs the data map to build a tab plan before field opens;
    it carries no respondent data."""
    assert may_download_datamap("analyst", questionnaire_approved=True).allowed is True
    assert may_download_datamap("reviewer", questionnaire_approved=True).allowed is True
