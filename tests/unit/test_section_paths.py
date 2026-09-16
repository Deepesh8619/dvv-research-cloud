"""Document-level routing: recruitment paths, intro screens, anchors, exclusivity.

Every case here comes from one real screener page (a parent/teen recruitment
path), each of which used to be read as something else entirely.
"""

from api.routes.fielding import _exclusive_only
from src.core.survey_definition import (
    Block,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.engines.questionnaire.routing_prose import parse_routing
from src.engines.questionnaire.section_paths import apply_intro_screens, apply_section_paths
from src.renderers.surveyjs_compiler import compile_to_surveyjs

S6_TEXT = """S6. Which retailers have you purchased skincare from in the past 6 months?
[PN: RANDOMIZE]
1. Ulta
2. Sephora
3. Target
4. None of the above [ANCHOR] [EXCLUSIVE]

Parent Recruitment Path Ages 26-60 (IF S0=1 AND S1=26-60) ASK P1-P3"""

P1_TEXT = """P1. Are you the parent or guardian of any teenagers in your household ages 13 to 17?
1. Yes
2. No [TERMINATE]
[PN: SHOW IF P1=1]
In this survey, we're looking to interview young women ages 13 to 17 about
their skincare habits, and we would like permission to speak with your teenager."""


def _survey(*chunks: tuple[str, str]) -> SurveyDefinition:
    questions = []
    for qid, text in chunks:
        fields = parse_prose_question(text, qid)
        questions.append(
            Question(
                qid=qid,
                text=fields.text or "",
                type=fields.qtype or QuestionType.SINGLE,
                options=fields.options,
                randomize_options=fields.randomize,
                show_if=fields.show_if,
                source_text=text,
            )
        )
    return SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", label="x", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def _full() -> SurveyDefinition:
    survey = _survey(
        ("S6", S6_TEXT),
        ("P1", P1_TEXT),
        ("P2", "P2. How old is your teenager?\n1. 13\n2. 14"),
        ("P3", "P3. Is your teenager available?\n1. Yes\n2. No [TERMINATE]"),
    )
    apply_section_paths(survey)
    apply_intro_screens(survey)
    return survey


def test_range_condition_parses_inclusively():
    condition = parse_routing("IF S0=1 AND S1=26-60")
    assert condition is not None
    assert evaluate(condition, {"S0": 1, "S1": 26}) is True
    assert evaluate(condition, {"S0": 1, "S1": 60}) is True
    assert evaluate(condition, {"S0": 1, "S1": 25}) is False
    assert evaluate(condition, {"S0": 1, "S1": 61}) is False
    assert evaluate(condition, {"S0": 2, "S1": 30}) is False


def test_show_if_preamble_is_read():
    assert parse_routing("PN: SHOW IF P1=1") is not None
    assert parse_routing("DISPLAY IF P1=1") is not None


def test_section_path_routes_the_whole_range():
    survey = _full()
    by_qid = {q.qid: q for q in survey.questions}
    for qid in ("P1", "P2", "P3"):
        assert by_qid[qid].show_if is not None, qid
        assert evaluate(by_qid[qid].show_if, {"S0": 1, "S1": 30}) is True
        assert evaluate(by_qid[qid].show_if, {"S0": 1, "S1": 24}) is False


def test_section_path_becomes_a_hidden_variable():
    survey = _full()
    hidden = [q for q in survey.questions if q.qid.startswith("HID_")]
    assert len(hidden) == 1
    assert hidden[0].type is QuestionType.INFO
    assert schema.FLAG_DERIVED_VARIABLE in hidden[0].flags
    assert evaluate(hidden[0].show_if, {"S0": 1, "S1": 40}) is True
    assert survey.metadata["section_paths"][0]["questions"] == ["P1", "P2", "P3"]


def test_own_base_is_kept_alongside_the_section_base():
    survey = _survey(
        ("S6", S6_TEXT),
        ("P2", "P2. How old is your teenager?\n[IF P1=1]\n1. 13\n2. 14"),
        ("P3", "P3. Available?\n1. Yes\n2. No"),
    )
    apply_section_paths(survey)
    p2 = next(q for q in survey.questions if q.qid == "P2")
    assert evaluate(p2.show_if, {"S0": 1, "S1": 30, "P1": 1}) is True
    assert evaluate(p2.show_if, {"S0": 1, "S1": 30, "P1": 2}) is False  # own base
    assert evaluate(p2.show_if, {"S0": 1, "S1": 24, "P1": 1}) is False  # section base


def test_intro_paragraph_is_a_screen_not_an_option():
    survey = _full()
    p1 = next(q for q in survey.questions if q.qid == "P1")
    assert [o.label for o in p1.options] == ["Yes", "No"]
    intro = next(q for q in survey.questions if q.qid == "INTRO_P1")
    assert intro.type is QuestionType.INFO
    assert "young women ages 13 to 17" in intro.text
    assert evaluate(intro.show_if, {"P1": 1}) is True
    assert evaluate(intro.show_if, {"P1": 2}) is False
    # A question is never its own base.
    assert "P1" not in (p1.show_if.referenced_qids() if p1.show_if else set())


def test_every_option_tag_is_read():
    fields = parse_prose_question(S6_TEXT, "S6")
    none_of = fields.options[-1]
    assert none_of.label == "None of the above"  # tags stripped from the label
    assert none_of.anchored is True
    assert none_of.exclusive is True
    assert fields.randomize is True


def test_anchored_option_stays_last_when_randomized():
    survey = _full()
    seen_first = set()
    for _ in range(50):
        element = next(
            e
            for page in compile_to_surveyjs(survey)["pages"]
            for e in page["elements"]
            if e.get("name") == "S6"
        )
        assert element["choices"][-1]["text"] == "None of the above"
        seen_first.add(element["choices"][0]["text"])
    assert len(seen_first) > 1  # the rest really do move


def test_exclusive_option_drops_co_selections_on_submit():
    survey = _full()
    assert _exclusive_only(survey, "S6", [1, 2, 4]) == [4]
    assert _exclusive_only(survey, "S6", [1, 2]) == [1, 2]
    assert _exclusive_only(survey, "S6", [4]) == [4]
    assert _exclusive_only(survey, "UNKNOWN", [1, 2]) == [1, 2]
    assert _exclusive_only(survey, "S6", 3) == 3


def test_no_routing_line_changes_nothing():
    survey = _survey(("Q1", "Q1. Favourite colour?\n1. Red\n2. Blue"))
    before = survey.to_json()
    assert apply_section_paths(survey) == []
    assert apply_intro_screens(survey) == []
    assert survey.to_json() == before


def test_long_option_labels_are_still_options():
    fields = parse_prose_question(
        "Q9. Where do you buy skincare?\n"
        "1. Directly from a skincare brand's own website or mobile application\n"
        "2. From a large online marketplace such as Amazon or Walmart dot com\n",
        "Q9",
    )
    assert len(fields.options) == 2
