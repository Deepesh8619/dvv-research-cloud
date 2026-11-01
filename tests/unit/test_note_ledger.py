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
    # "not" in an answer's label is not "do not randomise".
    done = _one("Randomize, anchor ‘Prefer not to answer’.", randomize_options=True)
    assert {r["outcome"] for r in done if r["kind"] == "randomize"} == {"applied"}


def _kind_outcome(note, **kw):
    return {(r["kind"]): r["outcome"] for r in _one(note, **kw)}


def test_applied_means_this_note_did_it():
    # Anchor set on code 1, but the note names code 2: another reader's work.
    assert _kind_outcome("Anchor punch 2 at the bottom.")["anchor"] == "unread"
    assert _kind_outcome("Anchor punch 1 at the bottom.")["anchor"] == "applied"
    # A made record overlapping the clause settles it.
    q = _one("Anchor punch 2 at the bottom.")
    assert q[0]["outcome"] == "unread"
    s = SurveyDefinition(study_id="s", questions=[Question(
        qid="Q1", text="?", type=QuestionType.MULTI, options=[Option(2, "B", anchored=True)],
        source_text="[PN: Anchor punch 2 at the bottom.]")])
    s.metadata["note_rules"] = [{"qid": "Q1", "set": "anchored 2", "said": "Anchor punch 2"}]
    assert read_notes(s)[0]["outcome"] == "applied"


def test_base_must_name_a_question_the_base_reads():
    from src.core.survey_definition import Expression
    base = Expression(op="in", qid="S5", values=[1])
    q = Question(qid="Q2", text="?", type=QuestionType.SINGLE, show_if=base,
                 source_text="[PN: Ask if punch 1 at S5.]\n[PN: Ask if punch 1 at S9.]")
    got = [r["outcome"] for r in read_notes(SurveyDefinition(study_id="s", questions=[q]))]
    assert got == ["applied", "unread"]


def test_gold_report(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "note_census", Path(__file__).parents[2] / "scripts" / "note_census.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    recs = [{"doc": "d", "qid": "Q1", "said": "a", "outcome": "applied"},
            {"doc": "d", "qid": "Q2", "said": "b", "outcome": "applied"},
            {"doc": "d", "qid": "Q3", "said": "c", "outcome": "unread"}]
    gold = tmp_path / "g.json"
    gold.write_text('[{"doc":"d","qid":"Q1","said":"a","outcome":"applied"},'
                    '{"doc":"d","qid":"Q2","said":"b","outcome":"unread"},'
                    '{"doc":"d","qid":"Q3","said":"c","outcome":"unread"}]')
    assert mod.gold_report(recs, gold, None) == 0
    assert "false applied 1" in capsys.readouterr().out
    assert mod.gold_report(recs, gold, 0) == 1
    assert mod.gold_report(recs, gold, 1) == 0
