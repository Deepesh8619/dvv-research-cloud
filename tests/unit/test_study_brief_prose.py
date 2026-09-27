"""The opening of a questionnaire states facts. Read them, then use them.

A document that opens with headings was read; a document that opens with
sentences — which is most of them outside programming shops — was not, and its
whole preamble survived as one display blob. The length of the interview, what
the study is asking about, and the term it defines up front were all facts
nothing had.
"""

from src.core.survey_definition import (
    Block,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.launch_check import loi_against_promise
from src.engines.questionnaire.definition_audit import audit_definition
from src.engines.questionnaire.study_overview import looks_like_overview, parse_overview
from src.intake.questionnaire_loader import DocumentBlock

_PREAMBLE = [
    "Greenbrook TMS — TRD Treatment Landscape",
    "This survey explores treatment options for MDD patients who have failed 2 "
    "or more antidepressants (commonly referred to as treatment-resistant "
    "depression or TRD)",
    "Please answer all questions as it relates to your patients and practice "
    "settings at Greenbrook only.",
    "The survey will take approximately 15 minutes to complete.",
]


def _blocks(lines):
    return [DocumentBlock("paragraph", line, i) for i, line in enumerate(lines)]


def test_a_preamble_written_as_sentences_is_an_overview():
    """Requiring ALL-CAPS headings read nothing on a document that has none."""
    assert looks_like_overview(_blocks(_PREAMBLE)) is True
    assert looks_like_overview(_blocks(["Q1. How old are you?"])) is False


def test_the_stated_interview_length_is_read_from_the_sentence():
    assert parse_overview(_blocks(_PREAMBLE)).loi_minutes == 15
    assert parse_overview(_blocks(["This takes about 8 minutes."])).loi_minutes == 8
    assert parse_overview(_blocks(["It will take 10-12 minutes"])).loi_minutes == 10


def test_what_the_study_is_asking_about_is_captured_separately():
    """A scope statement limits what every answer MEANS. It is not a routing
    condition and can never be enforced as one — the only thing that honours it
    is the respondent reading it."""
    overview = parse_overview(_blocks(_PREAMBLE))
    assert len(overview.scope_statements) == 1
    assert "Greenbrook only" in overview.scope_statements[0]
    assert any("treatment-resistant depression" in d for d in overview.definitions)


def test_a_scope_statement_the_respondent_never_sees_is_reported():
    """An intro tidied up, and every answer is about something wider than the
    client asked for — with data that looks exactly the same either way."""
    survey = SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="b1", question_ids=["Q1"])],
        questions=[Question(qid="Q1", text="How satisfied are you?",
                            type=QuestionType.SINGLE)],
    )
    survey.metadata["study_overview"] = {
        "scope_statements": ["Please answer only as it relates to Greenbrook"]
    }
    checks = {f.check for f in audit_definition(survey)}
    assert "scope_not_shown_to_respondent" in checks


def test_a_scope_statement_shown_on_an_intro_screen_is_not_reported():
    survey = SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="b1", question_ids=["INTRO", "Q1"])],
        questions=[
            Question(
                qid="INTRO",
                # Reproduced with different punctuation, as an intro screen is.
                text="Welcome. Please answer only as it relates to Greenbrook.",
                type=QuestionType.INFO,
            ),
            Question(qid="Q1", text="How satisfied?", type=QuestionType.SINGLE),
        ],
    )
    survey.metadata["study_overview"] = {
        "scope_statements": ["Please answer only as it relates to Greenbrook"]
    }
    checks = {f.check for f in audit_definition(survey)}
    assert "scope_not_shown_to_respondent" not in checks


def test_an_interview_running_far_over_its_promise_is_reported():
    """Both numbers existed and nothing compared them. The gap costs money in
    two directions: the panel is priced on the stated length, and respondents
    who notice abandon part-way."""
    survey = SurveyDefinition(study_id="t")
    survey.metadata["study_overview"] = {"loi_minutes": 15}
    over = loi_against_promise(survey, median_seconds=25 * 60)
    assert over["over"] is True
    assert over["stated_minutes"] == 15 and over["measured_minutes"] == 25.0
    assert "priced on the stated length" in over["note"]


def test_an_interview_running_about_as_promised_is_not_reported():
    """A questionnaire is written before anybody has timed it, so a few minutes
    either way is normal and flagging it would be noise."""
    survey = SurveyDefinition(study_id="t")
    survey.metadata["study_overview"] = {"loi_minutes": 15}
    assert loi_against_promise(survey, median_seconds=17 * 60)["over"] is False
    # Nothing stated, or nothing measured: no opinion either way.
    assert loi_against_promise(SurveyDefinition(study_id="t"), 900)["over"] is False
    assert loi_against_promise(survey, None)["measured_minutes"] is None


def test_a_single_stated_n_is_the_sample_size():
    overview = parse_overview(_blocks(["Online survey", "N=1,200 from Consumer Panel"]))
    assert overview.sample_size == 1200


def test_several_stated_n_values_are_not_guessed_between():
    """A real document states a panel figure, three age cells and a CRM range.
    Which is "the" sample size is a question the document does not answer, and a
    wrong fact stored in a schema reads as policy rather than as a guess."""
    overview = parse_overview(
        _blocks([
            "N=1,200 from Consumer Panel",
            "Teens (ages 13-17): N=400",
            "Early Gen Z/College (Ages 18-22): N=400",
            "N=600-800 from the CRM",
        ])
    )
    assert overview.sample_size is None
    # The cells are still captured as what they are.
    assert [q.group for q in overview.quotas] == [
        "Teens (ages 13-17)",
        "Early Gen Z/College (Ages 18-22)",
    ]
    assert overview.quotas[0].target_n == 400
