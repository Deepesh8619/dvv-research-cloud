"""Which answers are shown: anchoring, shuffling, masks and per-answer conditions."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.extractor import _randomize_from_cell
from src.engines.questionnaire.note_ledger import read_notes
from src.engines.questionnaire.note_rules import apply


def _q(qid, notes=(), codes=(1, 2, 3, 98), qtype=QuestionType.SINGLE, **kw):
    return Question(qid=qid, text=f"{qid}?", type=qtype,
                    options=[Option(code=c, label=f"A{c}") for c in codes],
                    source_text="\n".join(f"[PN: {n}]" for n in notes), **kw)


def _run(*qs):
    s = SurveyDefinition(study_id="s", source="x", status="draft",
                         blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                         questions=list(qs))
    apply(s)
    return {q.qid: q for q in qs}


def test_cell_that_says_not_to_shuffle_does_not_shuffle():
    # The spec table's "Randomize" cell read any negation but "do not" as a yes.
    for cell in ("Don't randomize", "Never randomise", "Ordered scale, do not rotate",
                 "Not to be randomized", "No", "", "Order as listed"):
        assert _randomize_from_cell(cell) is False, cell
    assert _randomize_from_cell("Yes") and _randomize_from_cell("Randomize, keep 98 last")


def test_do_not_randomize_wins_over_a_general_note():
    qs = _run(_q("Q1", ["Randomize response options.", "Do not randomize."],
                 randomize_options=True))
    assert qs["Q1"].randomize_options is False
    recs = [r for r in read_notes(SurveyDefinition(study_id="s", questions=[qs["Q1"]]))
            if r["kind"] == "randomize" and "Do not" in r["said"]]
    assert recs and recs[0]["outcome"] == "applied"


def test_do_not_randomize_one_answer_is_not_a_whole_list_note():
    qs = _run(_q("Q1", ["Randomize. Do not randomize punch 98."], randomize_options=True))
    assert qs["Q1"].randomize_options is True


def test_anchored_forms():
    qs = _run(_q("Q1", ["Keep punch 98 at the bottom."]),
              _q("Q2", ["Lock punch 3."]),
              _q("Q3", ["Fix punches 2 and 98."]),
              _q("Q4", ["Keep punch 2 and 3 together."]))
    assert [o.code for o in qs["Q1"].options if o.anchored] == [98]
    assert [o.code for o in qs["Q2"].options if o.anchored] == [3]
    assert [o.code for o in qs["Q3"].options if o.anchored] == [2, 98]
    assert not any(o.anchored for o in qs["Q4"].options)


def _mask(q):
    return ((q.validation or {}).get("techniques") or {}).get("masks")


def test_masks_from_earlier_question():
    qs = _run(_q("Q5", qtype=QuestionType.MULTI),
              _q("Q6", ["Only show those selected at Q5."]),
              _q("Q7", ["Show brands not selected at Q5."]),
              _q("Q8", ["Hide answers picked at Q5."]))
    assert [(m["source_qid"], m["mode"]) for m in _mask(qs["Q6"])] == [("Q5", "selected")]
    assert [(m["source_qid"], m["mode"]) for m in _mask(qs["Q7"])] == [("Q5", "not_selected")]
    assert [(m["source_qid"], m["mode"]) for m in _mask(qs["Q8"])] == [("Q5", "not_selected")]


def test_mask_source_later_or_missing_sets_nothing():
    qs = _run(_q("Q1", ["Only show those selected at Q5."]), _q("Q5"),
              _q("Q6", ["Only show those selected at Q99."]))
    assert not _mask(qs["Q1"]) and not _mask(qs["Q6"])


def test_option_hidden_by_condition():
    qs = _run(_q("S2"), _q("Q1", ["Do not show punch 3 if S2=1."]),
              _q("Q2", ["Do not show punch 3 if S9=1."]))
    shown = {o.code: o.show_if for o in qs["Q1"].options}
    assert shown[3].op == "not" and shown[3].children[0].qid == "S2"
    assert shown[1] is None and shown[98] is None
    assert all(o.show_if is None for o in qs["Q2"].options)   # S9 does not exist
