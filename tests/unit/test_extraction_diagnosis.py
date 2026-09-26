"""A study can be broken in a way that is already fixed.

An extraction is a snapshot taken by the parser of the day it ran. Nothing in
the stored definition can tell a reviewer that today's parser reads the same
document differently — only re-segmenting the archived source can.
"""

from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire.extraction_diagnosis import diagnose, naming_convention
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

_DOCUMENT = [
    DocumentBlock("paragraph", "[BASE: ALL] How satisfied are you with TMS?", 0),
    DocumentBlock("paragraph", "Very satisfied", 1),
    DocumentBlock("paragraph", "Not satisfied", 2),
    DocumentBlock("paragraph", "[BASE: ALL] How many medications had they failed?", 3),
    DocumentBlock("paragraph", "After 1 antidepressant treatment", 4),
    DocumentBlock("paragraph", "After 2 antidepressant treatments", 5),
]


def _stored_as_one_question() -> list[Question]:
    """What the study holds: the whole document as a single question."""
    return [
        Question(
            qid="Q1",
            text="How satisfied are you with TMS?",
            type=QuestionType.SINGLE,
            options=[Option(code=1, label="Very satisfied")],
            source_text="\n".join(b.text for b in _DOCUMENT),
        )
    ]


def test_it_names_the_gap_between_what_is_stored_and_what_the_parser_reads_now():
    report = diagnose(
        _stored_as_one_question(), segment_into_questions(_DOCUMENT), _DOCUMENT,
        can_reextract=True,
    )
    assert report["questions_stored"] == 1
    assert report["questions_the_parser_finds_now"] == 2
    # The tell from the real study: one stored question holding the document.
    assert report["whole_document_in_one_question"] == ["Q1"]
    assert any("never split into questions" in f for f in report["findings"])
    assert any("Re-extracting would pick up" in f for f in report["findings"])


def test_a_healthy_study_reports_nothing():
    """The check has to stay quiet on a study that is fine, or it is noise."""
    stored = [
        Question(qid="Q1", text="How satisfied are you with TMS?",
                 type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Very satisfied")],
                 source_text=_DOCUMENT[0].text),
        Question(qid="Q2", text="How many medications had they failed?",
                 type=QuestionType.SINGLE,
                 options=[Option(code=1, label="After 1 antidepressant treatment")],
                 source_text=_DOCUMENT[3].text),
    ]
    report = diagnose(stored, segment_into_questions(_DOCUMENT), _DOCUMENT,
                      can_reextract=True)
    assert report["findings"] == []


def test_it_names_the_convention_the_document_uses():
    """The useful answer is a fact about the document, not "the AI failed"."""
    assert "base tags" in naming_convention(_DOCUMENT)
    assert "numbered" in naming_convention(
        [DocumentBlock("paragraph", "Q5. How satisfied are you?", 0)]
    )
    assert "nothing recognisable" in naming_convention(
        [DocumentBlock("paragraph", "How satisfied are you?", 0)]
    )


def test_a_question_nobody_can_answer_is_reported():
    stored = [
        Question(qid="Q1", text="How satisfied are you?", type=QuestionType.SINGLE,
                 options=[], source_text=_DOCUMENT[0].text),
    ]
    report = diagnose(stored, segment_into_questions(_DOCUMENT), _DOCUMENT,
                      can_reextract=True)
    assert report["questions_with_nothing_to_answer"] == ["Q1"]


def test_an_unreachable_model_is_reported_as_an_outage_not_a_document_problem():
    stored = [
        Question(qid="Q1", text="", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Very satisfied")],
                 flags=["EXTRACTION_UNAVAILABLE"], source_text=_DOCUMENT[0].text),
        Question(qid="Q2", text="How many?", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="After 1")], source_text=_DOCUMENT[3].text),
    ]
    report = diagnose(stored, segment_into_questions(_DOCUMENT), _DOCUMENT,
                      can_reextract=True)
    assert report["questions_the_model_could_not_read"] == ["Q1"]
    assert any("backend outage, not a document problem" in f for f in report["findings"])


def test_without_an_archived_document_it_says_so_rather_than_guessing():
    report = diagnose(_stored_as_one_question(), [], _DOCUMENT, can_reextract=False)
    assert report["can_reextract"] is False
    assert "uploaded again" in report["note"]
