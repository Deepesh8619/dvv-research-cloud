"""The model for unread note clauses, with a fake backend and made-up lines."""

import logging

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import note_model
from src.engines.questionnaire.note_ledger import ledger

ZORK = "Zork the flibbet."
BLORP = "Blorp the wumble twice."


def _survey():
    opts = [Option(1, "A"), Option(2, "B")]
    qs = [Question(qid=q, text="Wording?", type=QuestionType.SINGLE, options=opts,
                   source_text=f"[PN: {n}]") for q, n in
          (("Q1", ZORK), ("Q2", ZORK), ("Q3", BLORP), ("Q4", "Ask all."))]
    s = SurveyDefinition(study_id="t", questions=qs)
    s.metadata["note_ledger"] = ledger(s)
    s.metadata["decoding"] = {"analyst": {"questions": []}}
    return s


def _fake(monkeypatch, records, seen=None):
    def run(prompt, schema, timeout=120):
        if seen is not None:
            seen.append(prompt)
        return AIResponse(content={"records": records}, backend="fake", model="m")
    monkeypatch.setattr(note_model, "run_private", run)


def _rec(**kw):
    return {"index": 0, "kind": "range", "qids": ["Q1"], "codes": [1], "says": "Limit it.", **kw}


def test_valid_record_becomes_a_question_and_asks_the_ledger(monkeypatch):
    s, seen = _survey(), []
    _fake(monkeypatch, [_rec()], seen)
    note_model.apply(s)
    assert seen[0].count(ZORK) == 1  # deduplicated by text
    asks = s.metadata["decoding"]["analyst"]["questions"]
    assert {a["qid"] for a in asks} == {"Q1", "Q2"}
    a = asks[0]
    assert a["kind"] == "note_model" and a["how"] == "model"
    assert a["ask"] == "This note seems to say: Limit it. Apply it?"
    assert a["options"] == ["Yes", "No, leave it for the scripter"]
    assert len(a["key"]) == 12 and a["key"] != asks[1]["key"]
    recs = {r["qid"]: r for r in s.metadata["note_ledger"]["records"]}
    assert recs["Q1"]["outcome"] == "asked" and recs["Q1"]["detail"] == "model suggestion"
    assert recs["Q3"]["outcome"] == "unread"
    assert s.metadata["note_ledger"]["summary"]["by_outcome"]["asked"] == 2
    assert len(s.metadata["note_model"]) == 2
    assert all(q.show_if is None and not q.validation for q in s.questions)


def test_invalid_records_are_dropped(monkeypatch):
    s = _survey()
    _fake(monkeypatch, [
        _rec(qids=["Q99"]), _rec(codes=[7]), _rec(kind="invented"), _rec(kind="none"),
        _rec(index=9), _rec(index="0"), _rec(codes=[1], qids=[]), "junk", _rec(says=" "),
    ])
    before = s.metadata["note_ledger"]
    note_model.apply(s)
    assert s.metadata["note_ledger"] == before and "note_model" not in s.metadata
    assert s.metadata["decoding"]["analyst"]["questions"] == []


def test_wrong_shape_or_no_backend_changes_nothing(monkeypatch):
    s = _survey()
    monkeypatch.setattr(note_model, "run_private", lambda *a, **k: AIResponse(
        content={"records": "x"}, backend="f", model="m"))
    note_model.apply(s)

    def down(*a, **k):
        raise BackendUnavailable("none", config_error=True)

    monkeypatch.setattr(note_model, "run_private", down)
    note_model.apply(s)
    assert "note_model" not in s.metadata
    assert s.metadata["note_ledger"]["summary"]["by_outcome"].get("asked") is None


def test_no_client_text_in_logs(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _fake(monkeypatch, [_rec(), _rec(index=1, qids=["Q404"])])
    note_model.apply(_survey())
    assert ZORK not in caplog.text and BLORP not in caplog.text
