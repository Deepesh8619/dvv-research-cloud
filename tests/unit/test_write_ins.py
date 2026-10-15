"""The write-in behind "Other (please specify)": parsed, compiled, stored, simulated."""

from __future__ import annotations

import pytest

from src.core.models import VarType
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.dryrun import simulate
from src.engines.fielding.materialize import materialize_fielding, plan_datamap
from src.engines.fielding.preflight import preflight
from src.engines.fielding.testdata import synthesise
from src.engines.questionnaire.write_ins import (
    is_write_in,
    mark_write_ins,
    write_in_name,
    write_in_prompt,
)
from src.orchestration.orchestrator import auto_tab_plan
from src.renderers.surveyjs_compiler import compile_to_surveyjs


@pytest.mark.parametrize("label", [
    "Other (Specify)", "Other (please specify)", "Other (please specify) ______",
    "Other (specify): [TEXTBOX]", "Other, specify: [textbox]", "Prefer to self-describe (Specify)",
    "98Prefer to self-describe (Specify)", "Another brand not listed (Specify)",
    "Other [OTHER SPECIFY]", "Other - please specify", "Please describe",
])
def test_write_in_labels_are_marked(label):
    assert is_write_in(label)


@pytest.mark.parametrize("label", [
    "Other", "Another brand", "None of the above", "Living with significant other",
    "Does not describe Bubble at all", "Describes Bubble very well",
    "Native Hawaiian or Other Pacific Islander",
    # a spec-table cell that leaked into an option label
    "Extremely ineffective Among who: Other >>> | If not All, specify: DA_PLACEMENT = 3",
    "Q type: Custom >>> | If Custom, specify: Sliding scale",
    "Other details: (blank)",
])
def test_bare_other_and_look_alikes_are_not(label):
    assert not is_write_in(label)


def _q(qid="Q1", qtype=QuestionType.MULTI, **kw):
    return Question(
        qid=qid, text="Which?", type=qtype,
        options=[Option(1, "Alpha"), Option(2, "Beta"), Option(98, "Other (Specify)"),
                 Option(99, "Other")], **kw)


def _survey(*questions):
    return SurveyDefinition(
        study_id="s", title="t", questions=list(questions),
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])])


def test_marking_sets_only_the_write_in_option():
    q = _q()
    assert mark_write_ins(_survey(q)) == [("Q1", 98)]
    assert [o.open_other for o in q.options] == [False, False, True, False]


def test_prompt_names_the_choice_only_when_it_says_more():
    assert write_in_prompt("Other (Specify)") == "Please specify"
    assert write_in_prompt("98Another brand (Specify)") == "Please specify (Another brand)"
    assert write_in_prompt("Prefer to self-describe (Specify)") == \
        "Please specify (Prefer to self-describe)"


def _marked(qtype=QuestionType.MULTI, **kw):
    q = _q(qtype=qtype, **kw)
    s = _survey(q)
    mark_write_ins(s)
    return s


def _box(compiled, name="Q1r98oe"):
    els = [e for p in compiled["pages"] for e in p["elements"]]
    return els, next((e for e in els if e["name"] == name), None)


def test_compile_multi_contains_and_required():
    els, box = _box(compile_to_surveyjs(_marked()))
    assert [e["name"] for e in els] == ["Q1", "Q1r98oe"]  # right after its question
    assert box["type"] == "text" and box["isRequired"] is True
    assert box["visibleIf"] == "{Q1} contains 98"


def test_compile_single_equals():
    _, box = _box(compile_to_surveyjs(_marked(QuestionType.SINGLE)))
    assert box["visibleIf"] == "{Q1} = 98"


def test_compile_no_box_without_the_flag():
    els, box = _box(compile_to_surveyjs(_survey(_q())))
    assert box is None and len(els) == 1


def test_compile_keeps_the_questions_own_base():
    from src.core.survey_definition import Expression

    s = _marked(show_if=Expression(op="eq", qid="S1", value=1))
    _, box = _box(compile_to_surveyjs(s))
    assert box["visibleIf"] == "({S1} = 1) and ({Q1} contains 98)"


def test_compile_loop_turns_each_get_their_own_box():
    s = _survey(_q("Q6_1"), _q("Q6_2"))
    mark_write_ins(s)
    names = [e["name"] for p in compile_to_surveyjs(s)["pages"] for e in p["elements"]]
    assert names == ["Q6_1", "Q6_1r98oe", "Q6_2", "Q6_2r98oe"]


def test_compile_masked_list_leaves_the_write_in_offered():
    # The mask must not hide "Other" (it is exempt), so the box can still appear.
    q = _q(validation={"techniques": {"masks": [
        {"source_qid": "Q0", "mode": "selected", "kind": "options"}]}})
    src = Question(qid="Q0", text="x", type=QuestionType.MULTI,
                   options=[Option(1, "Alpha"), Option(2, "Beta")])
    s = _survey(src, q)
    mark_write_ins(s)
    els = [e for p in compile_to_surveyjs(s)["pages"] for e in p["elements"]]
    main = next(e for e in els if e["name"] == "Q1")
    assert "98" in main.get("choicesVisibleIf", "98")  # exempt, or no mask at all
    assert any(e["name"] == "Q1r98oe" for e in els)


def test_compile_grid_row_box_waits_for_that_row():
    grid = Question(qid="G", text="g", type=QuestionType.GRID_SINGLE,
                    rows=[Option(1, "A"), Option(8, "Other (specify)")],
                    cols=[Option(1, "Yes"), Option(2, "No")])
    s = _survey(grid)
    mark_write_ins(s)
    _, box = _box(compile_to_surveyjs(s), "Gr8oe")
    assert box["visibleIf"] == "{G.8} notempty"


# ---------------------------------------------------------------- data
class _Session:
    def __init__(self, sid):
        self.id, self.status_code = sid, 10
        self.started_at = self.completed_at = "2026-01-01T09:00:00"
        self.entry_params = {}


def test_text_lands_in_datamap_and_data_only_when_picked():
    s = _marked()
    answers = {
        "a": {"Q1": [1, 98], "Q1r98oe": "  Gamma "},
        "b": {"Q1": [1], "Q1r98oe": "left behind after changing their mind"},
        "c": {"Q1": [98]},  # picked it, typed nothing the data can hold
    }
    dm, df = materialize_fielding(s, [_Session(i) for i in "abc"], answers)
    var = dm["Q1r98oe"]
    assert var.type is VarType.TEXT and var.question_ref == "Q1"
    assert df["Q1r98oe"].isna().tolist() == [False, True, True] and df["Q1r98oe"][0] == "Gamma"
    assert "Q1r98oe" in plan_datamap(s).names()


def test_single_question_text_follows_the_pick():
    s = _marked(QuestionType.SINGLE)
    _, df = materialize_fielding(
        s, [_Session("a"), _Session("b")],
        {"a": {"Q1": 98, "Q1r98oe": "mine"}, "b": {"Q1": 1, "Q1r98oe": "stale"}})
    assert df["Q1r98oe"].isna().tolist() == [False, True] and df["Q1r98oe"][0] == "mine"


def test_text_column_is_never_an_auto_table():
    s = _marked(QuestionType.SINGLE)
    dm, _ = materialize_fielding(s, [_Session("a")], {"a": {"Q1": 98, "Q1r98oe": "mine"}})
    # Even typed as a choice (a tabular re-import infers few distinct strings as one).
    dm["Q1r98oe"].type = VarType.SINGLE
    dm["Q1r98oe"].values = {1: "mine", 2: "yours"}
    plan = auto_tab_plan(dm)
    assert "Q1r98oe" not in [t.stub for t in plan.tables]
    assert "Q1" in [t.stub for t in plan.tables]


# ---------------------------------------------------------------- simulation
def test_dry_run_respondents_type_into_the_box_they_opened():
    s = _marked(QuestionType.SINGLE)
    report = simulate(s, n=60, seed=3, collect=True)
    picked = [a for a, _ in report.respondents if a.get("Q1") == 98]
    assert picked and all(a.get("Q1r98oe") for a in picked)
    assert all("Q1r98oe" not in a for a, _ in report.respondents if a.get("Q1") != 98)


def test_synthetic_dataset_carries_the_text_column():
    dm, df = synthesise(_marked(), n=60)
    assert "Q1r98oe" in dm.names()
    assert df["Q1r98oe"].notna().any()


def test_preflight_has_no_new_complaint_about_a_write_in():
    plain = preflight(_survey(_q()), approved=True)
    marked = preflight(_marked(), approved=True)
    assert {c["id"]: c["status"] for c in marked["checks"]} == \
        {c["id"]: c["status"] for c in plain["checks"]}


def test_name_follows_the_decipher_convention():
    from src.engines.qc.variable_roles import classify_variable

    assert classify_variable(write_in_name("SS_INDUSTRY", 98)) == "other_specify"


def test_reread_marks_the_write_in_too():
    from src.engines.questionnaire.extractor import reread_question

    old = Question(qid="Q1", text="x", type=QuestionType.SINGLE, options=[],
                   source_text="Q1. Which?\n1. Alpha\n2. Beta\n3. Other (please specify)")
    fresh = reread_question(old)
    assert fresh is not None
    assert [o.open_other for o in fresh.options] == [False, False, True]
