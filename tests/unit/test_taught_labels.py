"""Labels taught by one example: a new format is read without a code change."""

from src.engines.questionnaire.learned import validate_pattern
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.intake.taught_labels import shape_from_example


def _doc(lines: list[str]) -> list[DocumentBlock]:
    return [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
            for i, t in enumerate(lines)]


WORDED = _doc([
    "Brand tracker", "Thank you for taking part.",
    "Question 1: Which brands have you heard of?", "Alpha", "Beta",
    "Question 2: Which do you use most?", "Alpha", "Beta",
    "Question 3a: How satisfied are you?", "Very", "Not at all",
])

NAMED = _doc([
    "Intro text.",
    "QGENDER.", "Are you?", "Male", "Female",
    "QAGE.", "What is your age?",
    "QREGION.", "Where do you live?", "North", "South",
])


def labels(chunks):
    return [c.candidate_qid for c in chunks if c.candidate_qid]


def test_an_unknown_label_style_is_read_once_taught():
    assert labels(segment_into_questions(WORDED)) == []
    chunks = segment_into_questions(WORDED, ["Question 5:"])
    assert labels(chunks) == ["INTRO", "Q1", "Q2", "Q3a"]  # the thank-you is a screen
    q1 = next(c for c in chunks if c.candidate_qid == "Q1")
    assert q1.chunk_text.startswith("Which brands have you heard of?")


def test_a_label_on_its_own_line_names_the_line_after_it():
    chunks = segment_into_questions(NAMED, ["QGENDER."])
    assert labels(chunks) == ["QGENDER", "QAGE", "QREGION"]
    gender = next(c for c in chunks if c.candidate_qid == "QGENDER")
    assert gender.chunk_text.splitlines()[0] == "Are you?"


def test_a_taught_label_never_splits_a_document_written_another_way():
    """Fewer than three lines in that shape: the document does not use it."""
    assert labels(segment_into_questions(NAMED, ["Question 5:"])) == []
    assert labels(segment_into_questions(WORDED, ["QGENDER."])) == []


def test_ordinary_words_are_not_labels():
    shape = shape_from_example("QGENDER.")
    assert not shape.match("Thanks.") and not shape.match("Hello. there")
    assert shape_from_example("5.") is None  # no letter: not a label
    assert validate_pattern("question_label", "S5c.", "") is None
    assert validate_pattern("question_label", "x" * 40, "") is not None
