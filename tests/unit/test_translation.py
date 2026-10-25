"""Translation sheet keys, the token check, and the source fallback."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.translation import (
    check,
    source_strings,
    store,
    translated,
    valid_language,
)


def _survey():
    qs = [Question(qid="Q1", text="Rate {brand}", type=QuestionType.GRID_SINGLE,
                   rows=[Option(code=1, label="Price")],
                   cols=[Option(code=1, label="Bad"), Option(code=2, label="Good")]),
          Question(qid="HID_SEG", text="Segment", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="A")])]
    return SurveyDefinition(study_id="s", title="T", source="authored", status="draft", version=1,
                            blocks=[Block(block_id="B1", question_ids=["Q1", "HID_SEG"])],
                            questions=qs)


def test_grid_parts_are_keyed_and_hidden_variables_are_not_translated():
    keys = [k for k, _p, _t in source_strings(_survey())]
    assert keys == ["title", "Q1", "Q1/r/1", "Q1/c/1", "Q1/c/2"]


def test_untranslated_strings_fall_back_and_tokens_must_survive():
    s = _survey()
    problems = store(s, "fr", [{"key": "Q1", "translation": "Notez {marque}"},
                               {"key": "Q1/r/1", "translation": "Prix"},
                               {"key": "Q9", "translation": "?"}])
    assert {"key": "Q1", "problem": "keep {brand} exactly as in the source"} in problems
    assert {"key": "Q9", "problem": "not in this questionnaire"} in problems
    assert "Q9" not in s.metadata["translations"]["fr"]
    fr = translated(s, "fr")
    assert (fr.questions[0].rows[0].label, fr.questions[0].cols[0].label) == ("Prix", "Bad")
    assert s.questions[0].rows[0].label == "Price"  # the master is untouched
    assert translated(s, "de") is s
    assert check(s, {k: t for k, _p, t in source_strings(s)}) == []
    assert valid_language("pt-BR") and not valid_language("../x")


def test_a_draft_fills_blanks_keeps_tokens_and_stays_marked_until_a_person_checks_it():
    from types import SimpleNamespace

    from src.engines.fielding.translation import draft, sheet

    s = _survey()
    store(s, "hi", [{"key": "title", "translation": "शीर्षक"}])  # a person's, never overwritten

    def model(prompt, schema):
        assert "Hindi" in prompt
        return SimpleNamespace(content={"translations": [
            {"key": "title", "text": "OVERWRITE"},
            {"key": "Q1", "text": "रेट करें"},           # lost {brand}: refused
            {"key": "Q1/r/1", "text": "कीमत"},
            {"key": "Q1/c/1", "text": "बुरा"}, {"key": "Q1/c/2", "text": "अच्छा"}]})
    result = draft(s, "hi", run=model)
    assert result == {"drafted": 3, "refused": 1, "remaining": 1}
    texts = s.metadata["translations"]["hi"]
    assert texts["title"] == "शीर्षक" and "Q1" not in texts
    rows = {r["key"]: r for r in sheet(s, "hi")}
    assert rows["Q1/r/1"]["check"] == "model draft — check" and rows["title"]["check"] == ""

    # Sent back untouched, a draft is still a draft; edited, it is the translator's.
    back = [{**r, "translation": "दाम" if r["key"] == "Q1/r/1" else r["translation"]}
            for r in rows.values()]
    store(s, "hi", back)
    assert s.metadata["translation_drafts"]["hi"] == ["Q1/c/1", "Q1/c/2"]


def test_private_model_route_never_uses_a_free_tier(monkeypatch):
    import pytest

    from src.ai import router

    monkeypatch.setattr(router, "llm_key_presence", lambda: {
        "BEDROCK_API_KEY": False, "GROQ_API_KEY": True, "CEREBRAS_API_KEY": False,
        "HF_TOKEN": True})
    with pytest.raises(router.BackendUnavailable, match="only to Bedrock or a local model"):
        router.run_private("x", {})
    seen = []
    monkeypatch.setattr(router, "llm_key_presence", lambda: {
        "BEDROCK_API_KEY": True, "GROQ_API_KEY": True, "CEREBRAS_API_KEY": False,
        "HF_TOKEN": True})
    monkeypatch.setattr(router, "_run_chain", lambda chain, *a: seen.append(chain))
    router.run_private("x", {})
    assert [step["backend"] for step in seen[0]] == ["bedrock"]
