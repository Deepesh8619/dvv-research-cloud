"""Two things a respondent meets on screen: a question that is missing its
first answer, and a small picture telling them what the screen is about."""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.swallowed_options import repair_swallowed_options
from src.engines.questionnaire.symbols import KNOWN, symbol_for, symbols_for_survey
from src.renderers.surveyjs_compiler import compile_to_surveyjs

PHARMACY = (
    "Looking again at these pharmacies you've heard of before — have you ever "
    "personally used each one for your pharmacy needs? (such as prescription fills "
    "or refills, consultations with a pharmacist, or other pharmacy services, "
    "either in-person or through online/delivery) Yes"
)


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def test_the_answer_written_on_the_questions_own_line_is_given_back():
    """Question 18 of a live study offered "No" and nothing else: a respondent
    could answer No or abandon the interview."""
    q = Question(qid="Q18", text=PHARMACY, type=QuestionType.SINGLE,
                 options=[Option(code=2, label="No")])
    assert repair_swallowed_options(_survey(q)) == ["Q18"]
    assert q.text.endswith("online/delivery)")
    assert [(o.code, o.label) for o in q.options] == [(1, "Yes"), (2, "No")]
    assert "written on the end" in q.extraction_evidence["options"]


def test_a_question_whose_list_survived_is_left_alone():
    """Two or more answers means the document's own list came through, and a
    trailing word belongs to the wording."""
    q = Question(qid="Q2", text="Do you agree? Yes", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    assert repair_swallowed_options(_survey(q)) == []
    assert q.text == "Do you agree? Yes"


def test_a_trailing_word_that_is_not_the_missing_answer_is_not_invented():
    """Restoring an option nobody wrote would be worse than a short list."""
    q = Question(qid="Q3", text="Which pharmacy do you use most often? Any",
                 type=QuestionType.SINGLE, options=[Option(code=1, label="Boots")])
    assert repair_swallowed_options(_survey(q)) == []
    assert len(q.options) == 1


def test_a_question_is_marked_by_what_it_is_about_not_how_it_is_answered():
    pharmacy = Question(qid="Q1", text="How often do you visit a pharmacy?",
                        type=QuestionType.SINGLE, options=[Option(code=1, label="Weekly")])
    assert symbol_for(pharmacy) == "pharmacy"  # subject beats "how often"
    nps = Question(qid="Q2", text="How likely are you to recommend us to a friend "
                   "or colleague?", type=QuestionType.NUMERIC)
    assert symbol_for(nps) == "recommend"
    assert symbol_for(Question(qid="Q3", text="In which state do you live?",
                               type=QuestionType.SINGLE)) == "place"


def test_a_near_miss_word_does_not_take_the_wrong_picture():
    """Substring matching made "medicare" a care question and "brandy" a brand
    one. Whole words are what make a literal match safe."""
    assert symbol_for(Question(qid="Q4", text="Do you drink brandy in the evening?",
                               type=QuestionType.SINGLE)) == "drink"


def test_a_question_about_nothing_recognisable_still_gets_a_mark():
    marks = {
        symbol_for(Question(qid="Q5", text="Please continue.", type=t))
        for t in (QuestionType.OPEN_TEXT, QuestionType.RANK, QuestionType.GRID_SINGLE)
    }
    assert marks == {"comment", "rank", "grid"}


def test_every_mark_the_backend_can_send_is_one_the_front_end_knows():
    """A name outside the agreed set is a blank space on a respondent's screen."""
    assert all(symbol_for(q) in KNOWN for q in [
        Question(qid="A", text=PHARMACY, type=QuestionType.SINGLE),
        Question(qid="B", text="What is your age?", type=QuestionType.NUMERIC),
        Question(qid="C", text="", type=QuestionType.MULTI),
    ])


def test_the_marks_travel_with_the_compiled_survey():
    survey = _survey(
        Question(qid="Q1", text="What is your age?", type=QuestionType.NUMERIC),
        Question(qid="Q2", text="Which supermarket do you shop at?", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Tesco")]),
    )
    assert compile_to_surveyjs(survey)["questionSymbols"] == {"Q1": "person", "Q2": "shopping"}
    assert symbols_for_survey(survey) == {"Q1": "person", "Q2": "shopping"}


def test_the_front_end_can_draw_every_name_the_backend_emits():
    """A cross-language check, because the failure it prevents is silent: a
    symbol added here with no drawing there is a blank space on a respondent's
    screen, and nothing in either language's own tests would notice."""
    from pathlib import Path

    drawings = Path("web/src/components/respondent/symbols.tsx").read_text()
    missing = [name for name in sorted(KNOWN) if f"  {name}: (p)" not in drawings]
    assert not missing, f"no drawing in symbols.tsx for: {', '.join(missing)}"


def test_the_question_is_read_before_its_answer_labels():
    """Seen on screen: "Do you or anyone in your household work in any of these
    industries?" listed "IT / software" among its answers and came back marked
    as a question about devices. The answers say what can be picked; the
    question says what is being asked."""
    industries = Question(
        qid="Q2",
        text="Do you or anyone in your household work in any of these industries?",
        type=QuestionType.MULTI,
        options=[Option(code=i, label=label) for i, label in enumerate(
            ["Advertising, PR or media", "E-commerce or organised retail", "IT / software"], 1)],
    )
    assert symbol_for(industries) == "work"

    # The labels are still read when the wording itself says nothing.
    owned = Question(qid="Q4", text="Which of the following do you own?", type=QuestionType.MULTI,
                     options=[Option(code=1, label="Smartphone"), Option(code=2, label="Laptop")])
    assert symbol_for(owned) == "device"


def test_a_swallowed_answer_is_given_back_even_when_the_type_is_wrong():
    """Q4a of the live study: text ending "…online/delivery) Yes", one answer
    "No", and typed grid_single with neither rows nor columns. The repair was
    skipping it on type, so the question kept "No" as its only answer."""
    q = Question(qid="Q4a", text=PHARMACY, type=QuestionType.GRID_SINGLE,
                 options=[Option(code=2, label="No")])
    assert repair_swallowed_options(_survey(q)) == ["Q4a"]
    assert q.type is QuestionType.SINGLE
    assert [(o.code, o.label) for o in q.options] == [(1, "Yes"), (2, "No")]


def test_a_real_grid_is_never_taken_apart_by_that_repair():
    q = Question(qid="Q13", text="When did you last use each? Yes",
                 type=QuestionType.GRID_SINGLE,
                 rows=[Option(code=1, label="CVS")],
                 cols=[Option(code=1, label="Recently"), Option(code=2, label="No")])
    assert repair_swallowed_options(_survey(q)) == []
    assert q.type is QuestionType.GRID_SINGLE
