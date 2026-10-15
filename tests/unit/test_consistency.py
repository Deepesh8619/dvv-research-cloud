"""One positive and one negative fixture per consistency check."""

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import consistency as c
from src.engines.questionnaire.qa_loop import run_qa


def opts(*codes, **labels):
    return [Option(code=n, label=labels.get(f"c{n}", f"Option {n}")) for n in codes]


def q(qid="Q1", **kw):
    kw.setdefault("text", "Which?")
    kw.setdefault("type", QuestionType.SINGLE)
    return Question(qid=qid, **kw)


def hid(*defs, qid="HID_X"):
    return q(qid, type=QuestionType.INFO, flags=["DERIVED_VARIABLE"], options=[
        Option(code=i + 1, label=lab, definition=d) for i, (lab, d) in enumerate(defs)
    ])


def run(check, *questions):
    by = {x.qid: x for x in questions}
    return [i.check for i in check(questions[-1], by)]


def test_hid_unknown_question():
    src = q("C_OFFER", options=opts(1, 2))
    assert run(c.hidden_variable, src, hid(("A", "C_OFFER = 1"))) == []
    assert run(c.hidden_variable, src, hid(("A", "CP_OFFER = 1"))) == ["HID_UNKNOWN_QUESTION"]
    # a row number stuck to the name is not a different question
    assert run(c.hidden_variable, q("DA_USAGE"), hid(("A", "DA_USAGE5, 6 = 4"))) == []


def test_hid_unknown_code():
    src = q("S1", options=opts(1, 2, 3))
    assert run(c.hidden_variable, src, hid(("A", "S1 = 1-2"), ("B", "S1 = 3"))) == []
    got = run(c.hidden_variable, src, hid(("A", "S1 = 7")))
    assert got == ["HID_UNKNOWN_CODE"]


def test_hid_code_in_no_group_and_overlap():
    src = q("C_OFFER", options=opts(1, 2, 3, 99, c3="Both of these", c99="Prefer not to answer"))
    ok = hid(("R", "C_OFFER = 1"), ("S", "C_OFFER = 2,3"))
    assert run(c.hidden_variable, src, ok) == []
    bad = hid(("R", "C_OFFER = 1"), ("S", "C_OFFER = 2"))
    issues = c.hidden_variable(bad, {"C_OFFER": src})
    assert [i.check for i in issues] == ["HID_CODE_IN_NO_GROUP"]
    assert "3 (Both of these)" in issues[0].detail and "99" not in issues[0].detail
    assert run(c.hidden_variable, src, hid(("R", "C_OFFER = 1,2"), ("S", "C_OFFER = 2,3"))) == [
        "HID_CODE_IN_TWO_GROUPS"
    ]


def test_select_all_on_single():
    wording = q(text="Select all channels you use.")
    assert run(c.select_all_on_single, wording) == ["SELECT_ALL_ON_SINGLE"]
    multi = q(text="Select all channels.", type=QuestionType.MULTI)
    assert run(c.select_all_on_single, multi) == []
    assert run(c.select_all_on_single, q(text="Which one?")) == []


def test_directive_names_missing_code():
    note = "Randomize: Yes. Anchor 98. Make 99 exclusive"
    assert run(c.directive_names_missing_code, q(options=opts(1, 98, 99), source_text=note)) == []
    assert run(c.directive_names_missing_code, q(options=opts(1, 2), source_text=note)) == [
        "DIRECTIVE_CODE_MISSING"
    ] * 2


def test_conditional_wording_dropped():
    t = "How often [IF C1=13-17: (or had someone buy them for you)]?"
    assert run(c.conditional_wording_dropped, q(text=t)) == ["CONDITIONAL_WORDING_DROPPED"]
    # a pure note is hidden by the link and is not a finding
    assert run(c.conditional_wording_dropped, q(text="[ASK OF ALL] Which area?")) == []


def test_note_as_answer():
    bad = q(options=[Option(1, "SOCIAL"), Option(2, "RANDOMIZE ORDER. ENABLE ZOOM")])
    assert run(c.note_as_answer, bad) == ["NOTE_READ_AS_ANSWER"]
    pn = q(options=[Option(1, "[PN: RANDOMIZE]")])
    assert run(c.note_as_answer, pn) == ["NOTE_READ_AS_ANSWER"]
    assert run(c.note_as_answer, q(options=[Option(1, "DISPLAY"), Option(2, "Show me more")])) == []


def test_renamed_duplicate():
    assert run(c.renamed_duplicate, q("A_2", validation={"original_qid": "A"})) == ["QID_RENAMED"]
    assert run(c.renamed_duplicate, q("A_2", validation={"original_qid": "UNKNOWN"})) == []
    assert run(c.renamed_duplicate, q("A")) == []


def test_odd_qid():
    assert run(c.odd_qid, q("C_TEAM_S(ZE")) == ["QID_ODD_CHARACTER"]
    assert run(c.odd_qid, q("Q6_1")) == [] and run(c.odd_qid, q("Q2c.1")) == []


def test_numeric_without_range_is_info_only():
    stated = {"scripter_directives": ["ENTER NUMBER 1-100"]}
    n = q("S1", type=QuestionType.NUMERIC, text="Age?", validation=stated)
    assert [i.check for i in c.consistency_notes(n, {})] == ["NUMERIC_NO_RANGE"]
    assert "min 1, max 100" in c.consistency_notes(n, {})[0].detail
    assert c.consistency_check(n, {}) == []  # never a warning
    ranged = q("S1", type=QuestionType.NUMERIC, validation={"min": 0, "max": 9})
    assert c.consistency_notes(ranged, {}) == []


def test_gaps_in_codes_are_not_flagged():
    skipping = q(options=opts(1, 2, 3, 5, 6), source_text="Anchor 6")
    assert c.consistency_check(skipping, {"Q1": skipping}) == []


def test_run_qa_reports_hidden_variable_and_notes_without_flagging_them():
    n = q("S1", type=QuestionType.NUMERIC, text="Age?")
    h = hid(("A", "NOPE_Q = 1"))
    survey = SurveyDefinition(study_id="t", questions=[n, h])
    for _ in range(2):  # re-running must not duplicate
        record = run_qa(survey)
    assert "QA_ISSUE" in h.flags and len(h.validation["qa_issues"]) == 1
    assert "QA_ISSUE" not in n.flags and n.validation["consistency_notes"]
    assert [e["status"] for e in record["entries"] if e["qid"] == "S1"] == ["note"]


def test_a_terminating_answer_in_a_select_all_list_must_be_exclusive():
    from src.core.survey_definition import Expression, Option, Question, QuestionType
    from src.engines.questionnaire.consistency import terminating_answer_not_exclusive

    q = Question(qid="Q3", text="Which pharmacies have you heard of?", type=QuestionType.MULTI,
                 options=[Option(code=1, label="CVS"), Option(code=99, label="None of the above")],
                 terminate_if=Expression(op="in", qid="Q3", values=[99]))
    assert terminating_answer_not_exclusive(q, {})
    q.options[1].exclusive = True
    assert terminating_answer_not_exclusive(q, {}) == []


def test_a_screening_industry_ticked_with_others_is_not_flagged():
    from src.core.survey_definition import Expression, Option, Question, QuestionType
    from src.engines.questionnaire.consistency import terminating_answer_not_exclusive

    q = Question(qid="S2", text="Do you work in any of these industries?", type=QuestionType.MULTI,
                 options=[Option(code=1, label="Advertising"), Option(code=2, label="Healthcare")],
                 terminate_if=Expression(op="in", qid="S2", values=[1]))
    assert terminating_answer_not_exclusive(q, {}) == []
