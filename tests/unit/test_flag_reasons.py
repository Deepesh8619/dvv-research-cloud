"""Every flag carries a sentence naming its question and what was seen.

Cases are the three flag-quality problems found reading the Google Ads SMB
document: eight hidden variables flagged as unfaithful, flags with no reason
(SS_EMP), and an automatic answer (DA_TOP_GOAL) flagged as an unclear base.
"""

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire.flag_reasons import (
    flag_reason,
    flag_reasons,
    is_generic,
    mark_derived_facts,
)
from src.engines.questionnaire.verification import failed, verify_against_source


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="t", source="extracted", status="draft", questions=list(questions)
    )


# --- 1. hidden variables are not checked for dropped answers ---------------

HID_SOURCE = """Q Name: HID_B2B_B2C
Value definitions:
1: B2B  CP_CUST_TYPE = 1
2: B2C  CP_CUST_TYPE = 2
"""


def test_a_hidden_variables_derivation_lines_are_not_dropped_answers():
    hidden = Question(
        qid="HID_B2B_B2C",
        text="B2B or B2C",
        type=QuestionType.INFO,
        options=[Option(code=1, label="B2B CP_CUST_TYPE = 1")],
        flags=[schema.FLAG_DERIVED_VARIABLE],
    )
    assert "source_options_all_captured" not in {
        c.name for c in failed(verify_against_source(hidden, HID_SOURCE))
    }


def test_an_ordinary_question_still_fails_for_a_dropped_answer():
    asked = Question(
        qid="Q1",
        text="B2B or B2C",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="B2B CP_CUST_TYPE = 1")],
    )
    assert "source_options_all_captured" in {
        c.name for c in failed(verify_against_source(asked, HID_SOURCE))
    }


# --- 2. an entry mode that contradicts the type says so ---------------------

SS_EMP = """NUMBER OF EMPLOYEES
Q Name: SS_EMP
Response list:
1: Just me (sole proprietorship)
2: 2-9
99: Prefer not to answer
Other details: Allow 0-9999999 to be entered, whole numbers only"""


def _ss_emp(**kw) -> Question:
    base = dict(
        qid="SS_EMP",
        text="Approximately how many people are employed?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Just me"), Option(code=2, label="2-9")],
        source_text=SS_EMP,
    )
    base.update(kw)
    return Question(**base)


def test_typed_number_instruction_on_a_choice_list_is_flagged_with_the_reason():
    q = _ss_emp()
    mark_derived_facts(_survey(q))
    assert schema.FLAG_ENTRY_MODE_CONFLICT in q.flags
    reason = flag_reasons(q)[schema.FLAG_ENTRY_MODE_CONFLICT]
    assert "SS_EMP" in reason and "Allow 0-9999999" in reason and "choice list" in reason


def test_no_conflict_when_the_type_was_never_read_or_is_numeric():
    unread = _ss_emp(validation={"unresolved_fields": ["type"]})
    numeric = _ss_emp(type=QuestionType.NUMERIC, options=[])
    mark_derived_facts(_survey(unread, numeric))
    assert schema.FLAG_ENTRY_MODE_CONFLICT not in unread.flags + numeric.flags


# --- 3. an automatic answer is not an unclear base -------------------------

AUTO = (
    "Q Name: DA_TOP_GOAL\nAmong who: Other >>> | If not All, specify: "
    "auto=punch those who select only one at DA_GOALS"
)


def _top_goal(**kw) -> Question:
    base = dict(
        qid="DA_TOP_GOAL",
        text="Top goal?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Leads")],
        source_text=AUTO,
        flags=[schema.FLAG_AMBIGUOUS_ROUTING],
        validation={"routing_verbatim": "auto=punch those who select only one at DA_GOALS"},
    )
    base.update(kw)
    return Question(**base)


def test_auto_punch_is_recorded_and_no_longer_an_unclear_base():
    q = _top_goal()
    mark_derived_facts(_survey(q))
    assert q.validation["auto_answer"] == ["auto=punch those who select only one at DA_GOALS"]
    assert schema.FLAG_AMBIGUOUS_ROUTING not in q.flags
    assert "routing_verbatim" not in q.validation


def test_a_genuinely_unclear_base_beside_an_auto_answer_stays_flagged():
    q = _top_goal(validation={"routing_verbatim": "ASK IF they feel like it"})
    mark_derived_facts(_survey(q))
    assert "auto_answer" in q.validation
    assert schema.FLAG_AMBIGUOUS_ROUTING in q.flags


def test_the_spec_table_does_not_hand_other_arrows_to_a_model_as_the_base():
    from src.engines.questionnaire.spec_table import parse_spec_table
    from src.intake.questionnaire_loader import DocumentBlock

    rows = [
        ["Q Name", "DA_TOP_GOAL", "DA_TOP_GOAL"],
        [
            "Among who",
            "Other >>>",
            "If not All, specify: auto=punch those who select only one at DA_GOALS",
        ],
        ["Q type", "Std Single-Response (SR)", "If Custom, specify:"],
    ]
    fields = parse_spec_table(DocumentBlock("table", "", 0, rows=rows))
    assert not fields.base
    assert fields.evidence["auto_answer"].startswith("auto=punch")


# --- every flag that can be emitted explains itself ------------------------

ALL_CODES = [
    getattr(schema, n)
    for n in dir(schema)
    if n.startswith("FLAG_") and n != "FLAG_RECONCILIATION_UNAVAILABLE"
] + ["QA_ISSUE", "RECOVERED_FROM_DATAMAP"]


def test_every_flag_code_gets_a_sentence_that_names_the_question():
    for code in ALL_CODES:
        q = Question(qid="Q9", text="Something", type=QuestionType.SINGLE, flags=[code])
        reason = flag_reason(q, code)
        assert reason.startswith("Q9") and len(reason) > 25, (code, reason)


def test_reasons_use_what_the_question_recorded():
    q = Question(
        qid="Q9",
        text="x",
        type=QuestionType.SINGLE,
        flags=[schema.FLAG_AMBIGUOUS_ROUTING, schema.FLAG_VERIFICATION_DISCREPANCY],
        validation={
            "routing_verbatim": "ASK IF heavy user",
            "verification_checks": [
                {"check": "codes_match", "detail": "a code differs", "evidence": "Yes: 2"}
            ],
        },
    )
    reasons = flag_reasons(q)
    assert "ASK IF heavy user" in reasons[schema.FLAG_AMBIGUOUS_ROUTING]
    assert "codes_match" in reasons[schema.FLAG_VERIFICATION_DISCREPANCY]
    assert not any(is_generic(r) for r in reasons.values())


def test_the_review_payload_carries_the_reasons():
    from api.routes.questionnaire import _question_payload

    q = _ss_emp(flags=[schema.FLAG_AMBIGUOUS_ROUTING])
    payload = _question_payload(q, set())
    assert payload["flag_reasons"][schema.FLAG_AMBIGUOUS_ROUTING].startswith("SS_EMP")
