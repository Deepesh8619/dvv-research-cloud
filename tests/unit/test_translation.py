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
