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


def _kt(note, codes=None, **kw):
    codes = codes or tuple(range(1, 13))
    q = _run(_q("Q1", [note], codes=codes, randomize_options=True, **kw))["Q1"]
    return (q.validation or {}).get("keep_together"), q


def test_groups_kept_together_four_forms():
    got, _ = _kt("Randomize punch 1-11, keep punch 3-5 together (random order), punch 6-7 together")
    assert got == [{"codes": [3, 4, 5], "shuffle_within": True},
                   {"codes": [6, 7], "shuffle_within": True}]
    assert _kt("Show 1-3 together; randomize order within 1-3")[0] == [
        {"codes": [1, 2, 3], "shuffle_within": True}]
    assert _kt("Keep punch 9 and 10 together")[0] == [{"codes": [9, 10], "shuffle_within": True}]
    assert _kt("Show 4-5 together, randomize order within pair")[0][0]["codes"] == [4, 5]
    assert _kt("Show 4-5 together, in the same order")[0][0]["shuffle_within"] is False


def test_groups_with_missing_overlapping_or_anchored_codes_set_nothing():
    assert _kt("Keep punch 9 and 99 together")[0] is None
    assert _kt("Keep punch 3-5 together, punch 5-6 together")[0] is None
    _, q = _kt("Randomize, anchor punch 12. Keep punch 11 and 12 together")
    assert (q.validation or {}).get("keep_together") is None


def test_groups_reach_the_compiled_element_without_a_plain_shuffle():
    from src.renderers.surveyjs_compiler import compile_to_surveyjs

    q = _run(_q("Q1", ["Randomize, anchor punch 12. Keep punch 3-5 together."],
                codes=tuple(range(1, 13)), randomize_options=True))["Q1"]
    survey = SurveyDefinition(study_id="s", source="x", status="draft",
                              blocks=[Block(block_id="B", question_ids=["Q1"])], questions=[q])
    el = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    assert el["keepTogether"] == [{"codes": [3, 4, 5], "shuffle_within": True}]
    assert el["anchoredValues"] == [12]
    assert "choicesOrder" not in el
    assert [c["value"] for c in el["choices"]] == list(range(1, 13))
    assert [r["outcome"] for r in read_notes(survey) if "together" in r["said"]] == ["applied"]


def _labelled(qid, labels, notes, **kw):
    return Question(qid=qid, text=f"{qid}?", type=QuestionType.SINGLE,
                    options=[Option(code=i, label=x) for i, x in enumerate(labels, 1)],
                    source_text="\n".join(f"[PN: {n}]" for n in notes), randomize_options=True,
                    **kw)


_FREQ = ["Several times before", "Once or twice before", "I have not seen this before"]
_BRANDS = ["Acme", "Bolt", "Cirrus"]


def test_randomise_that_names_no_list_over_a_scale_is_asked_not_applied():
    from src.engines.questionnaire.reading_questions import analyst_reading

    q = _labelled("Q25", _FREQ, ["RANDOMIZE ORDER. ENABLE ZOOM ON IMAGES."])
    qs = _run(q)
    assert qs["Q25"].randomize_options is False
    assert qs["Q25"].validation["randomize_unclear"] == "RANDOMIZE ORDER."
    s = SurveyDefinition(study_id="s", questions=[q])
    rec = next(r for r in read_notes(s) if r["kind"] == "randomize")
    assert (rec["outcome"], rec["detail"]) == ("asked", "which list the randomise means")
    ask = next(a for a in analyst_reading(s)["questions"] if a["kind"] == "randomize")
    assert ask["ask"] == ('Q25 says "RANDOMIZE ORDER" but its answers are a scale. '
                          'What is randomised?')
    assert ask["options"] == ["The questions or images in this series", "The answers"]
    assert ask["key"] == next(a for a in analyst_reading(s)["questions"]
                              if a["kind"] == "randomize")["key"]


def test_randomise_still_shuffles_a_brand_list_a_named_list_and_numbered_scales():
    qs = _run(_labelled("Q1", _BRANDS, ["RANDOMIZE ORDER."]),
              _labelled("Q2", _FREQ, ["Randomize response options."]),
              _labelled("Q3", ["1 - Poor", "2", "3", "4", "5 - Great"], ["RANDOMIZE ORDER."]))
    assert qs["Q1"].randomize_options is True
    assert qs["Q2"].randomize_options is True
    assert qs["Q3"].randomize_options is False


def test_extracted_document_keeps_a_scale_in_order(monkeypatch):
    from src.ai.router import BackendUnavailable
    from src.engines.questionnaire import extractor as ex
    from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
    from src.renderers.surveyjs_compiler import compile_to_surveyjs

    def off(*a, **k):
        raise BackendUnavailable("AI off")

    monkeypatch.setattr(ex, "run_task", off)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    lines = ["Q25. Please indicate if you have seen this ad.", "[PN: RANDOMIZE ORDER.]",
             "1. Several times before", "2. Once or twice before",
             "3. I have not seen this before"]
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(lines, 1)]
    survey = ex.extract_survey_definition(segment_into_questions(blocks), "t")
    q = survey.questions[0]
    assert q.randomize_options is False and q.validation["randomize_unclear"]
    element = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    assert [c["value"] for c in element["choices"]] == [1, 2, 3]
    assert "choicesOrder" not in element
