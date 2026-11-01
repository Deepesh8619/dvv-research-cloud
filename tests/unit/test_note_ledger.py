"""The note ledger, on made-up questions."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.note_ledger import ledger, read_notes, summary


def _survey(*questions):
    return SurveyDefinition(study_id="t", questions=list(questions))


def _q(qid, notes, **kw):
    kw.setdefault("type", QuestionType.SINGLE)
    return Question(qid=qid, text="Wording?", source_text="\n".join(f"[PN: {n}]" for n in notes), **kw)  # noqa: E501


def _by_kind(records):
    return {r["kind"]: r["outcome"] for r in records}


def test_each_outcome():
    opts = [Option(1, "A", exclusive=True), Option(2, "B")]
    s = _survey(
        _q("Q1", ["Make punch 1 exclusive."], options=opts),                # applied
        _q("Q2", ["Terminate if the answer is under 18."]),                 # kind, no field
        _q("Q3", ["Quota check by region."]),                               # scripter
        _q("Q4", ["Ask all."]),                                             # by_design
        _q("Q5", ["Zork the flibbet."]),                                    # no kind
        _q("Q6", ["Create dummy variable BANDS: A IF Q1=1"], options=opts),  # asked or applied
    )
    kinds = {r["qid"]: r for r in read_notes(s) if r["qid"] != "Q6"}
    assert kinds["Q1"]["outcome"] == "applied"
    assert kinds["Q2"]["outcome"] == "unread"
    assert kinds["Q3"]["outcome"] == "scripter"
    assert kinds["Q4"]["outcome"] == "by_design"
    assert kinds["Q5"]["kind"] == "none" and kinds["Q5"]["outcome"] == "unread"
    hidden = [r for r in read_notes(s) if r["kind"] == "hidden_var"]
    assert hidden and hidden[0]["outcome"] in ("applied", "asked")


def test_blank_sentences_skipped_and_two_kinds_two_records():
    s = _survey(_q("Q1", ["", "  ", "Randomize the list. Quota by region."]))
    kinds = [r["kind"] for r in read_notes(s)]
    assert kinds == ["randomize", "quota"]
    both = read_notes(_survey(_q("Q2", ["Terminate and screen out if wrong."], terminate_if=None)))
    assert len(both) == 1  # one kind matched by two words stays one record
    two = read_notes(_survey(_q("Q3", ["Randomize the list and anchor punch 99."])))
    assert sorted(r["kind"] for r in two) == ["anchor", "randomize"]


def test_summary_and_cap():
    s = _survey(_q("Q1", ["Ask all.", "Zork."]))
    out = ledger(s)
    assert out["summary"]["total"] == 2
    assert out["summary"]["by_outcome"] == {"by_design": 1, "unread": 1}
    assert summary([])["total"] == 0


def test_checklist_has_one_entry_per_scripter_kind():
    # Made-up lines of the same shape; one per kind that resolves to scripter.
    lines = {
        "quota": "Quota check by region.",
        "layout": "Show on carousel, two per screen.",
        "stimulus": "Stimulus video for the concept.",
        "quality": "Flag if the respondent speeds.",
    }
    s = _survey(*[_q(f"Q{i}", [text]) for i, text in enumerate(lines.values(), start=1)])
    checks = ledger(s)["checks"]
    assert [c["kind"] for c in checks] == list(lines)
    assert all(set(c) == {"kind", "qid", "said"} for c in checks)
    assert checks[0] == {"kind": "quota", "qid": "Q1", "said": "Quota check by region."}


def test_layout_and_by_design_patterns_are_grouped_not_asked():
    s = _survey(
        _q("Q1", ["Show intermediate text only before this screen."]),
        _q("Q2", ["Show on carrousel."]),
        _q("Q3", ["Respondents can see the total."]),
        _q("Q4", ["Show to all."]),
    )
    records = read_notes(s)
    outcomes = {r["qid"]: (r["kind"], r["outcome"]) for r in records}
    assert outcomes["Q1"] == ("layout", "scripter")
    assert outcomes["Q2"] == ("layout", "scripter")
    assert outcomes["Q3"] == ("ask_all", "by_design")
    assert outcomes["Q4"] == ("ask_all", "by_design")
    assert not [r for r in records if r["outcome"] == "asked"]
    assert [c["qid"] for c in ledger(s)["checks"]] == ["Q1", "Q2"]


def test_repeats_of_one_sentence_on_one_question_appear_once():
    s = _survey(_q("Q1", ["Quota by region.", "Quota by region.", "Quota by region."]))
    assert len(read_notes(s)) == 3
    assert len(ledger(s)["checks"]) == 1


def _one(note, **kw):
    q = Question(qid="Q1", text="Q1?", type=QuestionType.MULTI,
                 options=[Option(code=1, label="A", anchored=True), Option(code=2, label="B")],
                 source_text=f"[PN: {note}]", **kw)
    survey = SurveyDefinition(study_id="s", source="x", status="draft",
                              blocks=[Block(block_id="B", question_ids=["Q1"])], questions=[q])
    return read_notes(survey)


def test_what_a_hand_check_found():
    assert _one("(blank)") == []
    assert _one("End screen.") == []
    # A mask, not "ask all".
    assert all(r["kind"] != "ask_all" for r in _one("Show all response options EXCEPT Q5."))
    # Another answer being anchored says nothing about keeping two together.
    assert all(r["outcome"] != "applied" for r in _one("Keep punch 1 and 2 together."))
    shuffled = _one("Randomize, keep 1 and 2 together.", randomize_options=True)
    assert shuffled[0]["outcome"] == "unread"
    held = {"keep_together": [{"codes": [1, 2], "shuffle_within": True}]}
    held_for = _one("Randomize, keep 1 and 2 together.", randomize_options=True, validation=held)
    assert held_for[0]["outcome"] == "applied"
    other = _one("Randomize, keep 1 and 3 together.", randomize_options=True, validation=held)
    assert other[0]["outcome"] == "unread"
    # "not" in an answer's label is not "do not randomise".
    done = _one("Randomize, anchor ‘Prefer not to answer’.", randomize_options=True)
    assert {r["outcome"] for r in done if r["kind"] == "randomize"} == {"applied"}
