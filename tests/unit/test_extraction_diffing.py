"""Before/after diffing of two extractions (correction loop, P4).

The load-bearing tests here are the ones asserting that a rule CANNOT look
better than it is: a rewritten-but-still-wrong field must not score as a win,
and a question that improved one field while breaking another must surface as
a regression. Review is only meaningful if the diff refuses to flatter.
"""

from __future__ import annotations

from src.engines.questionnaire.diffing import diff_extractions


def _q(qid="Q1", **fields):
    base = {"qid": qid, "text": "Which brands?", "type": "single", "options": [], "flags": []}
    base.update(fields)
    return base


def test_field_found_where_there_was_none_is_improved():
    """The owner's case: a terminate that was missed is now detected."""
    before = [_q(terminate_if=None)]
    after = [_q(terminate_if={"op": "eq", "qid": "S1", "value": 5})]
    d = diff_extractions(before, after).to_dict()
    assert d["counts"]["improved"] == 1
    assert d["questions"][0]["changes"][0]["field"] == "terminate_if"
    assert d["questions"][0]["changes"][0]["verdict"] == "improved"


def test_field_lost_is_regressed():
    before = [_q(terminate_if={"op": "eq", "qid": "S1", "value": 5})]
    after = [_q(terminate_if=None)]
    d = diff_extractions(before, after).to_dict()
    assert d["counts"]["regressed"] == 1


def test_rewritten_value_is_changed_not_improved():
    """A rule that rewrites text into DIFFERENT wrong text must not score a win.

    Under a naive "it changed, so it worked" metric this is the failure that
    lets a harmful rule through review.
    """
    before = [_q(text="Which brands do you use?")]
    after = [_q(text="Something else entirely")]
    d = diff_extractions(before, after).to_dict()
    assert d["counts"]["changed"] == 1
    assert d["counts"]["improved"] == 0


def test_mixed_question_counts_as_regressed():
    """Gained a terminate but lost its options — that is not a win."""
    before = [_q(terminate_if=None, options=[{"code": 1, "label": "Yes"}])]
    after = [_q(terminate_if={"op": "eq", "qid": "S1", "value": 5}, options=[])]
    d = diff_extractions(before, after).to_dict()
    assert d["counts"]["regressed"] == 1
    assert d["counts"]["improved"] == 0


def test_whitespace_and_case_only_change_is_not_a_change():
    before = [_q(text="Which  Brands?")]
    after = [_q(text="which brands?")]
    assert diff_extractions(before, after).counts()["unchanged"] == 1


def test_question_vanishing_is_a_regression():
    """The most consequential thing a bad rule can do."""
    d = diff_extractions([_q("Q1"), _q("Q2")], [_q("Q1")]).to_dict()
    assert d["counts"]["regressed"] == 1
    assert d["questions"][0]["qid"] == "Q2"
    assert d["questions"][0]["only_in"] == "before"


def test_question_recovered_is_an_improvement():
    d = diff_extractions([_q("Q1")], [_q("Q1"), _q("Q2")]).to_dict()
    assert d["counts"]["improved"] == 1
    assert d["questions"][0]["only_in"] == "after"


def test_flags_cleared_is_improvement_added_is_regression():
    cleared = diff_extractions(
        [_q(flags=["AMBIGUOUS_ROUTING"])], [_q(flags=[])]
    ).counts()
    assert cleared["improved"] == 1

    added = diff_extractions([_q(flags=[])], [_q(flags=["EMPTY_OPTIONS"])]).counts()
    assert added["regressed"] == 1


def test_regressions_are_listed_first():
    """A reviewer must meet the damage before the wins."""
    before = [_q("Q1", terminate_if={"op": "eq"}), _q("Q2", terminate_if=None)]
    after = [_q("Q1", terminate_if=None), _q("Q2", terminate_if={"op": "eq"})]
    d = diff_extractions(before, after).to_dict()
    assert d["questions"][0]["verdict"] == "regressed"
    assert d["questions"][0]["qid"] == "Q1"


def test_unchanged_questions_are_counted_but_not_listed():
    """74 unchanged rows would bury the 6 that matter."""
    d = diff_extractions([_q("Q1"), _q("Q2")], [_q("Q1"), _q("Q2")]).to_dict()
    assert d["counts"]["unchanged"] == 2
    assert d["questions"] == []
    assert d["n_questions"] == 2


def test_identical_extractions_report_no_change():
    """A rule that changes nothing must be visibly inert, not vaguely positive."""
    qs = [_q("Q1", terminate_if={"op": "eq"}), _q("Q2", options=[{"code": 1, "label": "Y"}])]
    counts = diff_extractions(qs, [dict(q) for q in qs]).counts()
    assert counts == {"improved": 0, "regressed": 0, "changed": 0, "unchanged": 2}


# --- Honest verdicts after a re-read renumbers, cleans, or adds information --


def _opt(*labels):
    return [{"code": i, "label": label} for i, label in enumerate(labels, start=1)]


def test_a_renumbered_question_is_compared_with_itself():
    """A recovered question shifts every number after it. Matched by number,
    Q15 was compared with what had been Q14 and read as a regression."""
    before = [_q("Q4", text="How satisfied are you with TMS?", options=_opt("Yes", "No")),
              _q("Q5", text="Which factors matter most to your patients?", options=_opt("A", "B"))]
    after = [_q("Q4", text="How satisfied are you with TMS?", options=_opt("Yes", "No")),
             _q("Q5", text="A question that was recovered", options=_opt("X", "Y")),
             _q("Q6", text="Which factors matter most to your patients?", options=_opt("A", "B"))]
    d = diff_extractions(before, after).to_dict()
    moved = next(q for q in d["questions"] if q["qid"] == "Q6")
    assert moved["renumbered_from"] == "Q5" and moved["verdict"] == "unchanged"
    assert d["counts"]["regressed"] == 0


def test_answers_that_only_lost_separators_are_improved():
    before = [_q(options=_opt("Safety |", "Other (specify) [ANCHOR] |"))]
    after = [_q(options=_opt("Safety", "Other (specify)"))]
    assert diff_extractions(before, after).to_dict()["counts"]["improved"] == 1


def test_a_grid_whose_options_became_rows_and_a_scale_is_not_a_regression():
    before = [_q(type="grid_single", options=_opt("COLUMNS:", "0 | 1 | 2"))]
    after = [_q(type="grid_single", options=[], rows=_opt("TMS", "Spravato"),
                cols=_opt("Not at all", "Extremely"))]
    assert diff_extractions(before, after).to_dict()["counts"]["improved"] == 1


def test_a_question_that_can_no_longer_be_answered_is_still_a_regression():
    before = [_q(options=_opt("Yes", "No"))]
    after = [_q(options=[])]
    assert diff_extractions(before, after).to_dict()["counts"]["regressed"] == 1


def test_an_informational_flag_is_not_harm():
    before = [_q(options=_opt("Yes", "No"))]
    after = [_q(options=_opt("Yes", "No"), flags=["BRIEF_RULE"])]
    assert diff_extractions(before, after).to_dict()["counts"]["regressed"] == 0


def test_quality_is_measured_the_same_way_on_both_sides():
    before = [_q("Q1", options=[]), _q("Q2", options=_opt("Yes", "No"))]
    after = [_q("Q1", options=_opt("Yes", "No")), _q("Q2", options=_opt("Yes", "No"))]
    quality = diff_extractions(before, after).to_dict()["quality"]
    assert quality["before"]["answerable_pct"] == 50
    assert quality["after"]["answerable_pct"] == 100
