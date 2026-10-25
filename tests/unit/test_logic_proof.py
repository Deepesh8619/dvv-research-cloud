"""The solver proof: it must agree with `evaluate` and with the dry run."""

from __future__ import annotations

import time

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.decisions import is_visible, terminating_question
from src.engines.fielding.dryrun import simulate
from src.engines.questionnaire.library import TEMPLATES, build_template
from src.engines.questionnaire.logic_proof import prove, summary

BRAND_INPUTS = {"organisation": "DVV Research", "purpose": "coffee choices",
                "contact": "privacy@dvv.example", "category": "instant coffee",
                "brands": "Nescafé\nBru\nDavidoff\nSunrise", "subject": "Nescafé",
                "attributes": "Taste\nPrice\nPackaging\nAroma"}


def ex(**kw) -> Expression:
    return Expression(**kw)


def single(qid, codes=(1, 2, 3), **kw) -> Question:
    return Question(qid=qid, text=qid, type=QuestionType.SINGLE,
                    options=[Option(code=c, label=str(c)) for c in codes], **kw)


def survey(*qs: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="s", title="t", questions=list(qs),
                            blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])])


def replay(sv: SurveyDefinition, answers: dict, target: str) -> bool:
    """Walk an example through the engine's rules in question order; True if `target` shows."""
    got: dict = {}
    for qid in sv.question_order():
        if qid == target:
            break
        q = sv[qid]
        if q.type == QuestionType.INFO:
            if qid in answers:
                got[qid] = answers[qid]
            continue
        if not is_visible(q, got):
            assert qid not in answers, f"example answers {qid}, which is hidden"
            continue
        if qid in answers:
            got[qid] = answers[qid]
        assert terminating_question(sv, got) is None, f"screened out at {qid}"
    return is_visible(sv[target], got)


def check_examples(sv: SurveyDefinition, proof: dict) -> None:
    for qid, answers in proof["examples"].items():
        assert replay(sv, answers, qid), qid


def _hand_built() -> list[SurveyDefinition]:
    a = survey(
        single("Q1"), single("Q2", show_if=ex(op="eq", qid="Q1", value=2)),
        single("Q3", show_if=ex(op="not", children=[ex(op="in", qid="Q2", values=[1, 2])])),
        single("Q4", show_if=ex(op="and", children=[ex(op="ne", qid="Q1", value=3),
                                                    ex(op="selected", qid="Q1", value=1)])))
    multi = Question(qid="M1", text="m", type=QuestionType.MULTI, options=[
        Option(code=1, label="a"), Option(code=2, label="b"),
        Option(code=9, label="none", exclusive=True)])
    b = survey(
        Question(qid="A", text="age", type=QuestionType.NUMERIC,
                 validation={"min": 10, "max": 90},
                 terminate_if=ex(op="lt", qid="A", value=18)),
        multi, single("B1", show_if=ex(op="selected", qid="M1", value=9)),
        single("B2", show_if=ex(op="and", children=[ex(op="selected", qid="M1", value=1),
                                                    ex(op="selected", qid="M1", value=2)])),
        single("B3", show_if=ex(op="gt", qid="A", value=60)))
    grid = Question(qid="G", text="g", type=QuestionType.GRID_SINGLE,
                    rows=[Option(code=1, label="r1"), Option(code=2, label="r2")],
                    cols=[Option(code=i, label=str(i)) for i in range(1, 6)])
    gm = Question(qid="GM", text="gm", type=QuestionType.GRID_MULTI,
                  rows=[Option(code=1, label="r1")], cols=[Option(code=1, label="a"),
                                                          Option(code=2, label="b")])
    c = survey(grid, gm,
               single("C1", show_if=ex(op="eq", qid="G", row=2, value=5)),
               single("C2", show_if=ex(op="selected", qid="GM", row=1, value=2)),
               single("C3", show_if=ex(op="ne", qid="G", value=99)))
    return [a, b, c]


def test_examples_replay_and_cover_the_dry_run():
    template = survey(*build_template("brand_tracker", BRAND_INPUTS, set()))
    assert "brand_tracker" in TEMPLATES
    for sv in [template, *_hand_built()]:
        proof = prove(sv)
        check_examples(sv, proof)
        shown = set(sv.question_order()) - set(simulate(sv, n=1000).questions_never_shown)
        reached = {q for q in shown if sv[q].type != QuestionType.INFO}
        assert reached <= set(proof["examples"]) | {p["qid"] for p in proof["not_provable"]}, \
            reached - set(proof["examples"])
        assert not proof["unreachable"], proof["unreachable"]


def test_exclusive_option_and_conjunction_are_decided_exactly():
    proof = prove(_hand_built()[1])
    assert proof["unreachable"] == [] and set(proof["examples"]) >= {"B1", "B2", "B3"}
    # Both options ticked plus the exclusive one is impossible; ticking 1 and 2 is not.
    assert sorted(proof["examples"]["B2"]["M1"]) == [1, 2]


def test_one_in_ten_thousand_is_proved_though_the_dry_run_misses_it():
    sv = survey(Question(qid="Q1", text="n", type=QuestionType.NUMERIC,
                         validation={"min": 0, "max": 9999}),
                single("Q2", show_if=ex(op="eq", qid="Q1", value=4242)))
    assert "Q2" in simulate(sv, n=1000, seed=1).questions_never_shown
    proof = prove(sv)
    assert proof["examples"]["Q2"]["Q1"] == 4242 and not proof["unreachable"]
    check_examples(sv, proof)


def test_a_contradiction_is_unreachable_and_the_reason_names_both():
    sv = survey(single("Q5"), single("Q7", show_if=ex(op="eq", qid="Q5", value=4)))
    proof = prove(sv)
    [gone] = proof["unreachable"]
    assert gone["qid"] == "Q7"
    assert "Q7 needs Q5 = 4" in gone["why"] and "Q5 only allows 1–3" in gone["why"]
    assert "Q7 can never be shown" in summary(proof)


def test_unanswered_leaf_is_false_and_not_over_it_is_true():
    # Q3 is shown when Q2 is NOT 1; Q2 only exists when Q1 = 1. The engine hides Q3 for
    # anyone who skipped Q2 (a base is judged only once its questions are answered).
    sv = survey(single("Q1"), single("Q2", show_if=ex(op="eq", qid="Q1", value=1)),
                single("Q3", show_if=ex(op="not", children=[ex(op="eq", qid="Q2", value=1)])))
    proof = prove(sv)
    assert proof["examples"]["Q3"]["Q1"] == 1 and proof["examples"]["Q3"]["Q2"] != 1
    check_examples(sv, proof)


def test_screen_outs_chain_and_derived_variables_are_not_provable():
    sv = survey(
        single("S1", codes=(1, 2), terminate_if=ex(op="eq", qid="S1", value=1)),
        single("Q2", show_if=ex(op="eq", qid="S1", value=1)),
        single("Q3", terminate_if=ex(op="gt", qid="Q3", value=5)),
        single("Q4", show_if=ex(op="eq", qid="HID_X", value=1)))
    sv.questions.append(Question(qid="HID_X", text="x", type=QuestionType.INFO,
                                 options=[Option(code=1, label="a", derive_if=ex(
                                     op="eq", qid="S1", value=2))]))
    sv._rebuild_index()
    proof = prove(sv)
    assert [u["qid"] for u in proof["unreachable"]] == ["Q2"]
    assert "screens out" in proof["unreachable"][0]["why"]
    assert [n["qid"] for n in proof["not_provable"]] == ["Q4"]
    assert proof["impossible_screenouts"] == [{"qid": "Q3"}]
    assert "Q2" not in proof["examples"] and "S1" in proof["examples"]


def test_every_screen_out_is_flagged_when_everyone_is_screened():
    sv = survey(single("S1", codes=(1,), terminate_if=ex(op="eq", qid="S1", value=1)),
                single("Q2"))
    proof = prove(sv)
    assert proof["always_screened_out"] == ["S1"]
    assert [u["qid"] for u in proof["unreachable"]] == ["Q2"]


def test_150_questions_prove_in_under_five_seconds():
    qs: list[Question] = []
    for i in range(150):
        cond = None
        if i:
            cond = ex(op="or", children=[ex(op="eq", qid=f"Q{i - 1}", value=1),
                                         ex(op="in", qid=f"Q{max(i - 2, 0)}", values=[2, 3])])
        qs.append(single(f"Q{i}", show_if=cond,
                         terminate_if=ex(op="eq", qid=f"Q{i}", value=9) if i % 10 == 0 else None))
    sv = survey(*qs)
    t = time.time()
    proof = prove(sv)
    assert time.time() - t < 5, time.time() - t
    assert len(proof["examples"]) + len(proof["unreachable"]) == 150
    check_examples(sv, proof)


def _plain(qid, type_=QuestionType.SINGLE, codes=(1, 2, 3), **kw):
    return Question(qid=qid, text=qid, type=type_,
                    options=[Option(code=c, label=str(c)) for c in codes], **kw)


def _of(*qs):
    return SurveyDefinition(study_id="s", title="t", questions=list(qs),
                            blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])])


def test_a_follow_up_on_a_question_with_no_options_names_that_as_the_reason():
    """Seen on a real document: the parser kept a single choice with no codes."""
    s = _of(_plain("CONSIDER", codes=()),
            _plain("REASON", show_if=Expression(op="in", qid="CONSIDER", values=[1, 2])))
    [u] = prove(s)["unreachable"]
    assert u["qid"] == "REASON" and "CONSIDER has no answer options" in u["why"]


def test_a_comparison_on_an_unmodelled_answer_is_not_provable_not_a_crash():
    s = _of(_plain("NOTE", type_=QuestionType.OPEN_TEXT, codes=()),
            _plain("Q2", show_if=Expression(op="gt", qid="NOTE", value=3)))
    p = prove(s)
    assert [n["qid"] for n in p["not_provable"]] == ["Q2"] and not p["unreachable"]
