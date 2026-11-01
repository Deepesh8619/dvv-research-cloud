"""Answers that cannot go together, and more masks: made-up wording, AI off, compiled."""

from src.ai.router import BackendUnavailable
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire import note_ledger
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _survey(monkeypatch, lines):
    def off(*a, **k):
        raise BackendUnavailable("AI off")

    monkeypatch.setattr(ex, "run_task", off)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(lines, 1)]
    return ex.extract_survey_definition(segment_into_questions(blocks), "t")


def _elements(survey):
    out = {}
    for page in compile_to_surveyjs(survey)["pages"]:
        for e in page.get("elements", []):
            out[e["name"]] = e
    return out


def _list(qid, text, *labels, note=None):
    lines = [f"{qid}. {text}"]
    if note:
        lines.append(f"[PN: {note}]")
    return lines + [f"{i}. {lab}" for i, lab in enumerate(labels, 1)]


def test_never_together_is_a_validator(monkeypatch):
    lines = (_list("Q1", "Which do you use? Select all that apply.", "Alpha", "Beta", "Gamma",
                   note="Do not allow punch 1 and 2 to be selected together.")
             + _list("Q2", "Which one do you like?", "Alpha", "Beta",
                     note="Do not allow punch 1 and 2 to be selected together."))
    survey = _survey(monkeypatch, lines)
    q1, q2 = survey.questions
    assert q1.validation["never_together"] == [[1, 2]]
    assert "never_together" not in (q2.validation or {})
    el = _elements(survey)
    v = [x for x in el["Q1"]["validators"] if "notcontains" in x["expression"]]
    assert v and v[0]["text"] == "Please do not choose both Alpha and Beta."
    assert "validators" not in el["Q2"]
    kinds = {(r["qid"], r["outcome"]) for r in note_ledger.read_notes(survey)
             if r["kind"] == "not_together"}
    assert kinds == {("Q1", "applied"), ("Q2", "unread")}


def test_never_together_unknown_code_is_unread(monkeypatch):
    survey = _survey(monkeypatch, _list(
        "Q1", "Which do you use? Select all that apply.", "Alpha", "Beta",
        note="Do not allow punch 1 and 7 to be selected together."))
    assert "never_together" not in (survey.questions[0].validation or {})


def test_list_except_reaches_the_page(monkeypatch):
    lines = (_list("S6", "Which one is yours?", "Alpha", "Beta", "Gamma")
             + _list("Q1", "Which would you pick?", "Alpha", "Beta", "Gamma",
                     note="Show all response options EXCEPT for option selected at S6.")
             + _list("Q0", "Which now?", "Alpha", "Beta",
                     note="Show all response options EXCEPT for option selected at S6."))
    survey = _survey(monkeypatch, lines)
    qs = {q.qid: q for q in survey.questions}
    mask = qs["Q1"].validation["techniques"]["masks"][0]
    assert mask["mode"] == "list_except" and mask["exclude_qid"] == "S6"
    other = ((qs["Q0"].validation or {}).get("techniques") or {}).get("masks") or []
    assert all(m["mode"] != "list_except" for m in other)  # S6 has a code Q0 lacks
    assert "{S6} <>" in _elements(survey)["Q1"]["choicesVisibleIf"]
    assert {r["outcome"] for r in note_ledger.read_notes(survey)
            if r["kind"] == "mask" and r["qid"] == "Q1"} == {"applied"}


def test_except_with_later_source_sets_nothing(monkeypatch):
    lines = (_list("Q1", "Which would you pick?", "Alpha", "Beta",
                   note="Show all response options EXCEPT for option selected at S6.")
             + _list("S6", "Which one is yours?", "Alpha", "Beta"))
    survey = _survey(monkeypatch, lines)
    masks = (survey.questions[0].validation.get("techniques") or {}).get("masks") or []
    assert all(m["mode"] != "list_except" for m in masks)


def test_paired_options_follow_their_sources(monkeypatch):
    note = "Only show punches 1, 2, and 3 if corresponding service was selected (punch 2) at S5, S6, and S7."  # noqa: E501
    lines = (_list("S5", "First?", "No", "Yes") + _list("S6", "Second?", "No", "Yes")
             + _list("S7", "Third?", "No", "Yes")
             + _list("Q1", "Which are relevant?", "Alpha", "Beta", "Gamma", note=note))
    survey = _survey(monkeypatch, lines)
    choices = _elements(survey)["Q1"]["choices"]
    assert [c["visibleIf"] for c in choices if "visibleIf" in c]
    assert "S5" in choices[0]["visibleIf"] and "S7" in choices[2]["visibleIf"]


def test_paired_unequal_or_unnamed_punch_is_unread(monkeypatch):
    unnamed = "Only show punches 1, 2, and 3 if corresponding service was selected at S5, S6, and S7."  # noqa: E501
    short = "Only show punches 1, 2, and 3 if corresponding service was selected (punch 2) at S5 and S6."  # noqa: E501
    lines = (_list("S5", "First?", "No", "Yes") + _list("S6", "Second?", "No", "Yes")
             + _list("S7", "Third?", "No", "Yes")
             + _list("Q1", "Which?", "Alpha", "Beta", "Gamma", note=unnamed)
             + _list("Q2", "Which?", "Alpha", "Beta", "Gamma", note=short))
    survey = _survey(monkeypatch, lines)
    el = _elements(survey)
    assert not any("visibleIf" in c for c in el["Q1"]["choices"] + el["Q2"]["choices"])
    assert {r["outcome"] for r in note_ledger.read_notes(survey)
            if r["kind"] == "mask" and r["qid"] in ("Q1", "Q2")} == {"unread"}


def test_show_codes_needs_a_condition(monkeypatch):
    lines = (_list("S2", "Gate?", "No", "Yes")
             + _list("Q1", "Which?", "Alpha", "Beta", "Gamma", note="Mask: show codes 1-2")
             + _list("Q2", "Which?", "Alpha", "Beta", "Gamma",
                     note="Show codes 1-2 if S2=2."))
    survey = _survey(monkeypatch, lines)
    el = _elements(survey)
    assert not any("visibleIf" in c for c in el["Q1"]["choices"])
    assert [("visibleIf" in c) for c in el["Q2"]["choices"]] == [True, True, False]
    out = {r["qid"]: r["outcome"] for r in note_ledger.read_notes(survey) if r["kind"] == "mask"}
    assert out == {"Q1": "unread", "Q2": "applied"}
