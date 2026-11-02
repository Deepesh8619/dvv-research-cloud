"""C2: similar settled cases in the prompts. Made-up lines only, fake backends."""

import json
from unittest.mock import patch

from src.ai.router import AIResponse
from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import correction_examples as ce
from src.engines.questionnaire import extractor, note_model
from src.engines.questionnaire.extractor import extract_question, extract_survey_definition
from src.engines.questionnaire.note_ledger import ledger
from src.intake.questionnaire_loader import QuestionChunk

ZORK = "Zork the flibbet."
FLIB = "Flibbet wumble gronk zork rule applies here."


def _row(text=FLIB, study="other", fields=("required",), **kw):
    return {"study_id": study, "source_text": text,
            "before": {"required": False, "text": "old"},
            "after": {"required": True, "text": "new"},
            "changed_fields": list(fields), **kw}


def _chunk(text="Q1. Flibbet wumble gronk zork?"):
    return QuestionChunk(chunk_text=text, candidate_qid="Q1", source_blocks=[], provenance="p")


def _content():
    return {"qid": "Q1", "text": "T?", "type": "single",
            "options": [{"code": 1, "label": "Y"}, {"code": 2, "label": "N"}],
            "routing": {"verbatim_text": "", "ambiguous": False, "show_if": None,
                        "terminate_if": None},
            "confidence": {"text": 0.9, "type": 0.9, "options": 0.9, "routing": 0.9}}


def _read(rows, text="Q1. Flibbet wumble gronk zork?", study="s", fn=None):
    seen = []

    def run(task, prompt, schema, **kw):
        seen.append(prompt)
        return AIResponse(content=_content(), backend="fake", model="m")

    with patch("src.engines.questionnaire.extractor.run_task", run):
        extract_survey_definition([_chunk(text)], study_id=study,
                                  correction_rows=fn or (lambda: rows))
    return seen[0]


def test_similar_ranks_and_drops_unrelated():
    rows = [_row("Banana smoothie recipe."), _row(FLIB), _row("Flibbet only.")]
    got = ce.similar("flibbet wumble gronk", rows, 5)
    assert [r["source_text"] for r in got] == [FLIB, "Flibbet only."]
    assert ce.similar("flibbet wumble gronk", rows, 1) == [rows[1]]


def test_prompt_block_shows_only_changed_fields():
    b = ce.prompt_block([_row()])
    assert b.startswith(ce.HEADER) and "OTHER questions" in b
    assert 'reader said: {"required":false}' in b and 'correct: {"required":true}' in b
    assert "new" not in b and "old" not in b
    assert ce.prompt_block([]) == ""


def test_prompt_block_clips_and_drops_whole_examples():
    rows = [_row("flibbet " + "x" * 5000, fields=("text",)) for _ in range(10)]
    b = ce.prompt_block(rows)
    assert len(b) <= ce.BLOCK_CAP
    assert b.count("reader said:") == len(ce.fit(rows)) < 10
    assert "x" * 601 not in b
    assert b.count("Text:") == b.count("correct:")  # no example cut in half


def test_identical_same_study_text_is_excluded():
    rows = [_row("Q1. Flibbet wumble gronk zork?", study="s"), _row(FLIB, study="other")]
    got = ce.similar("Q1. Flibbet wumble gronk zork?", rows, 3, "s")
    assert [r["study_id"] for r in got] == ["other"]
    assert "Q1. Flibbet wumble gronk zork?" not in _read(rows[:1], study="s").split(
        ce.HEADER)[-1].split("SOURCE CHUNK")[0]


def test_question_prompt_contains_example_and_empty_is_byte_identical():
    assert ce.HEADER in _read([_row()])
    assert _read([]) == _read(None, fn=lambda: [])
    plain = []
    with patch("src.engines.questionnaire.extractor.run_task",
               lambda t, p, s, **k: plain.append(p) or AIResponse(
                   content=_content(), backend="f", model="m")):
        extract_survey_definition([_chunk()], study_id="s")
    assert plain[0] == _read([])


def test_failing_loader_does_not_stop_extraction():
    def boom():
        raise RuntimeError("db gone")
    prompt = _read(None, fn=boom)
    assert ce.HEADER not in prompt


def test_loader_called_once_and_never_when_model_is_off():
    calls = []

    def fn():
        calls.append(1)
        return [_row()]
    seen = []
    with patch("src.engines.questionnaire.extractor.run_task",
               lambda t, p, s, **k: seen.append(p) or AIResponse(
                   content=_content(), backend="f", model="m")):
        extract_survey_definition([_chunk(), _chunk()], study_id="s", correction_rows=fn)
    assert len(calls) == 1 and ce.HEADER in seen[0]
    tok = extractor._MODEL_OFF.set(True)
    try:
        calls.clear()
        extract_question(_chunk(), examples_for=lambda t: calls.append(1) or "")
    finally:
        extractor._MODEL_OFF.reset(tok)
    assert calls == []


def _survey():
    opts = [Option(1, "A"), Option(2, "B")]
    qs = [Question(qid="Q1", text="W?", type=QuestionType.SINGLE, options=opts,
                   source_text=f"[PN: {ZORK}]")]
    s = SurveyDefinition(study_id="s", questions=qs)
    s.metadata["note_ledger"] = ledger(s)
    s.metadata["decoding"] = {"analyst": {"questions": []}}
    return s


def _fake(monkeypatch, records, seen):
    def run(prompt, schema, timeout=120):
        seen.append(prompt)
        return AIResponse(content={"records": records}, backend="fake", model="m")
    monkeypatch.setattr(note_model, "run_private", run)


def test_note_model_prompt_has_example_and_counts(monkeypatch):
    seen = []
    _fake(monkeypatch, [], seen)
    s = _survey()
    note_model.apply(s, [_row("Zork the flibbet quickly."), _row("Banana.")])
    assert ce.HEADER in seen[0] and "Zork the flibbet quickly." in seen[0]
    assert s.metadata["examples_used"]["note_model"] == 1
    json.dumps(s.metadata["examples_used"])


def test_note_model_without_rows_is_byte_identical(monkeypatch):
    a, b = [], []
    _fake(monkeypatch, [], a)
    note_model.apply(_survey())
    note_model.apply(_survey(), [])
    _fake(monkeypatch, [], b)
    note_model.apply(_survey())
    assert a[0] == b[0] and ce.HEADER not in a[0] and ce.HEADER not in a[1]


def test_example_naming_q99_does_not_make_q99_acceptable(monkeypatch):
    seen = []
    rec = {"index": 0, "kind": "range", "qids": ["Q99"], "codes": [1], "says": "Limit it."}
    _fake(monkeypatch, [rec], seen)
    s = _survey()
    note_model.apply(s, [_row("Zork the flibbet quickly. Q99 only.",
                              before={"text": "Q99"}, after={"text": "Q99"})])
    assert "Q99" in seen[0]
    assert s.metadata.get("note_model") is None
    assert s.metadata["decoding"]["analyst"]["questions"] == []


def test_a_deployment_can_turn_examples_off(monkeypatch):
    from src.engines.questionnaire.corrections_store import example_dicts
    monkeypatch.setenv("DVV_CORRECTION_EXAMPLES", "off")
    assert example_dicts(None) == []
