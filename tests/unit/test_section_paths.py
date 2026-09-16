"""Document-level routing: recruitment paths, intro screens, anchors, exclusivity.

Every case here comes from one real screener page (a parent/teen recruitment
path), each of which used to be read as something else entirely.
"""

from api.routes.fielding import _exclusive_only
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
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


def test_inserted_questions_are_addressable():
    """A question inserted after assembly must join the survey's qid index —
    otherwise the compiler and the datamap both look straight past it, and the
    intro screen never reaches the respondent."""
    survey = _full()
    assert "INTRO_P1" in survey
    assert survey["INTRO_P1"].type is QuestionType.INFO
    hidden = next(q for q in survey.questions if q.qid.startswith("HID_"))
    assert hidden.qid in survey


def test_exclusive_tag_implies_a_multi_punch():
    """EXCLUSIVE is meaningless in a radio list — it says the question is multi.
    Typed single, S6's list answer landed in one column as NULL and every punch
    was lost."""
    fields = parse_prose_question(S6_TEXT, "S6")
    assert fields.qtype is QuestionType.MULTI


def test_variables_exist_at_approval_and_hidden_ones_carry_data():
    from src.engines.fielding.materialize import materialize_fielding, plan_datamap

    survey = _full()
    names = [v.name for v in plan_datamap(survey).variables]
    hidden = next(q.qid for q in survey.questions if q.qid.startswith("HID_"))
    assert names[0] == "respondent_id"
    assert {"S6r1", "S6r2", "S6r3", "S6r4", hidden, "P1"} <= set(names)
    assert "INTRO_P1" not in names  # a display screen is not a variable

    class _Session:
        def __init__(self, sid, code):
            self.id, self.status_code = sid, code
            self.started_at, self.completed_at = "2026-09-16T09:00:00", "2026-09-16T09:05:00"

    _, frame = materialize_fielding(
        survey,
        [_Session("a", 10), _Session("b", 10)],
        {
            "a": {"S0": 1, "S1": 41, "S6": [1, 3], "P1": 1},
            "b": {"S0": 1, "S1": 24, "S6": [4]},
        },
    )
    rows = frame.set_index("respondent_id")
    assert list(rows[hidden]) == [1, 0]  # in path / outside the age band
    assert list(rows["S6r1"]) == [1, 0]
    assert list(rows["S6r4"]) == [0, 1]


def test_a_note_line_is_never_an_answer():
    """"Note: ..." under an option list became answer code 1 on a live study."""
    fields = parse_prose_question(
        "S5. Who makes healthcare decisions in your household?\n"
        "1. Me\n"
        "2. My partner\n"
        "Note: Healthcare decisions include researching where to seek care and "
        "determining if care is needed.\n",
        "S5",
    )
    assert [o.label for o in fields.options] == ["Me", "My partner"]


def test_a_long_option_with_an_abbreviation_survives():
    """"e.g." ends in a period without ending a sentence. Counting it as one
    made an ordinary long answer look like prose — which, for any option after
    the first, would have dropped it from the list entirely."""
    fields = parse_prose_question(
        "Q17c. Where do you fill prescriptions?\n"
        "1. A national chain pharmacy such as CVS or Walgreens\n"
        "2. Pharmacy associated with a healthcare system that I use (e.g. Northwell, "
        "New York-Presbyterian, NYU Langone, Mount Sinai or another local system)\n",
        "Q17c",
    )
    assert len(fields.options) == 2
    assert "Northwell" in fields.options[1].label


def test_a_base_written_inside_the_question_line_is_read():
    """"D3. [ASK IF C1=18+] What is your marital status?" — only directive-ONLY
    lines were read for routing, so this was dropped entirely. On one live study
    twelve questions stated a base and not one was applied."""
    fields = parse_prose_question(
        "D3. [ASK IF C1=18+] What is your marital status?\n1. Married\n2. Single", "D3"
    )
    assert fields.show_if is not None
    assert evaluate(fields.show_if, {"C1": 18}) is True
    assert evaluate(fields.show_if, {"C1": 17}) is False


def test_punch_lists_keep_their_reading_aids_out_of_the_data():
    """"Q15=1 (ANUA) OR 11 (MEDICUBE) OR 98" — the codes are the condition; the
    brand names in brackets are there to help a human read it."""
    condition = parse_routing("ASK IF Q15=1 (ANUA) OR 11 (MEDICUBE) OR 98")
    assert condition is not None
    assert condition.values == [1, 11, 98]


def test_a_forward_ask_states_another_questions_base():
    """"[PN: ASK Q15 IF Q14 = 1 OR 2]" sits on Q14 and governs Q15."""
    survey = _survey(
        ("Q14", "Q14. Did you switch brands?\n[PN: ASK Q15 IF Q14 = 1 OR 2]\n1. Yes\n2. No"),
        ("Q15", "Q15. Which brand did you switch to?\n1. Anua\n2. Medicube"),
    )
    apply_section_paths(survey)
    q15 = next(q for q in survey.questions if q.qid == "Q15")
    assert q15.show_if is not None
    assert evaluate(q15.show_if, {"Q14": 1}) is True
    assert evaluate(q15.show_if, {"Q14": 3}) is False


def test_the_audit_catches_a_base_the_document_states_but_nothing_applied():
    from src.engines.questionnaire.definition_audit import audit_definition

    unrouted = Question(
        qid="Q17",
        text="What challenges have you had?",
        options=[Option(code=1, label="Cost")],
        source_text="Q17.\t[SHOW IF QP=1] When managing prescriptions...",
    )
    checks = {f.check for f in audit_definition(_survey_of(unrouted))}
    assert "base_stated_but_not_applied" in checks

    unrouted.show_if = Expression(op="in", qid="QP", values=[1])
    checks = {f.check for f in audit_definition(_survey_of(unrouted))}
    assert "base_stated_but_not_applied" not in checks


def _survey_of(*questions) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def test_an_exact_condition_wrapped_in_prose_is_read():
    """"[ASK IF AWARE OF CVS (Q3=2)]" — the scripter explains the base in words
    and writes the exact one in brackets. The parenthetical IS the condition."""
    condition = parse_routing("ASK IF AWARE OF CVS (Q3=2)")
    assert condition is not None
    assert evaluate(condition, {"Q3": 2}) is True
    assert evaluate(condition, {"Q3": 1}) is False


def test_prose_conditions_are_still_refused():
    """No exact condition is stated, so none may be invented. Applying a guess
    here sends a question to the wrong people and nothing downstream can tell."""
    assert parse_routing("ASK IF MULTIPLE AT S6 SELECTED") is None
    assert parse_routing("SHOW IF FILLED AT LEAST ONE PRESCRIPTION TYPE AT Q14") is None


def test_a_condition_on_an_option_is_reported_as_its_own_thing():
    """A missed question base is a parser bug worth re-extracting for; a base on
    one ANSWER OPTION is a feature the platform lacks. Same symptom, different
    fix — reporting them alike sends people looking for the wrong one."""
    from src.engines.questionnaire.definition_audit import audit_definition

    q = Question(
        qid="S12",
        text="Do you have children?",
        options=[Option(code=1, label="Yes - for a child under 18")],
        source_text=(
            "S12. Do you have children?\n"
            "1. Yes - for a child under 18 [ONLY SHOW IF S11=1]\n2. No"
        ),
    )
    checks = {f.check for f in audit_definition(_survey_of(q))}
    assert checks == {"option_base_unsupported"}


def test_an_option_carrying_a_display_condition_is_not_thrown_away():
    """"Yes - for a child under 18 [ONLY SHOW IF S11=1]" matched the section
    routing rule on its bare "IF S11=1" and was discarded as a header — so a
    live study's instrument was missing that answer entirely: five options in
    the document, four in the definition, and no flag to say so."""
    fields = parse_prose_question(
        "S12. Do you handle prescriptions for someone else? Select all that apply.\n"
        "[PN: RANDOMIZE]\n"
        "Yes - for a child under 18 [ONLY SHOW IF S11=1]\n"
        "Yes - for a parent/elderly relative\n"
        "No, I do not [ANCHOR, EXCLUSIVE]",
        "S12",
    )
    assert [o.label for o in fields.options] == [
        "Yes - for a child under 18",
        "Yes - for a parent/elderly relative",
        "No, I do not",
    ]
    assert fields.options[-1].anchored and fields.options[-1].exclusive


def test_a_section_header_is_still_not_an_option():
    fields = parse_prose_question(
        "S6. Which retailers?\n1. Ulta\n2. Sephora\n\n"
        "Parent Recruitment Path Ages 26-60 (IF S0=1 AND S1=26-60) ASK P1-P3",
        "S6",
    )
    assert [o.label for o in fields.options] == ["Ulta", "Sephora"]
