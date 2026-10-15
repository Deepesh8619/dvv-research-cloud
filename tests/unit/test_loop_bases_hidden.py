"""A loop turn's base, read from the hidden variable its base points at."""

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.loop_bases import resolve_loop_bases_from_hidden


def _hidden(qid, *labels):
    return Question(qid=qid, text="h", type=QuestionType.INFO, flags=["DERIVED_VARIABLE"],
                    options=[Option(code=i, label=lab) for i, lab in enumerate(labels, 1)])


def _turn(qid, brand, base):
    return Question(qid=qid, text="Agree?", type=QuestionType.GRID_SINGLE,
                    flags=["AMBIGUOUS_ROUTING"], validation={"loop": {"label": brand}},
                    extraction_evidence={"base": f"Among who: {base}"})


def test_each_turn_gets_the_hidden_value_naming_its_brand_and_state():
    aware = _hidden("HID_AWARE", "Google Ads Aware", "Meta Ads Aware", "TikTok Aware")
    google = _hidden("HID_GOOGLE_USER", "Google Ads Current User", "Google Ads Lapsed User")
    meta = _hidden("HID_META_USER", "Meta Ads Current User", "Meta Ads Lapsed User")
    att = [_turn(f"ATT_{i}", b, "Provider aware based on HID_AWARE")
           for i, b in enumerate(["Google Ads", "TikTok"], 1)]
    mot = [_turn(f"MOT_{i}", b, "Platform current user")
           for i, b in enumerate(["Google Ads", "Meta Ads"], 1)]
    unknown = _turn("ATT_9", "Snapchat", "Provider aware based on HID_AWARE")
    s = SurveyDefinition(study_id="s", questions=[aware, google, meta, *att, *mot, unknown])
    resolve_loop_bases_from_hidden(s)
    got = {q.qid: (q.show_if.qid, q.show_if.values) if q.show_if else None for q in s.questions}
    assert got["ATT_1"] == ("HID_AWARE", [1]) and got["ATT_2"] == ("HID_AWARE", [3])
    assert got["MOT_1"] == ("HID_GOOGLE_USER", [1]) and got["MOT_2"] == ("HID_META_USER", [1])
    assert got["ATT_9"] is None and "AMBIGUOUS_ROUTING" in unknown.flags


def test_a_base_written_for_one_brand_applies_only_to_that_turn():
    s = SurveyDefinition(study_id="s", questions=[
        _hidden("HID_GOOGLE_USER", "Current", "Lapsed", "Aware Non-User"),
        _turn("NB_1", "Google Ads",
              "Google Ads Lapsed or Aware Non-User (HID_GOOGLE_USER = 2 or 3)"),
        _turn("NB_2", "Meta Ads",
              "Google Ads Lapsed or Aware Non-User (HID_GOOGLE_USER = 2 or 3)"),
    ])
    resolve_loop_bases_from_hidden(s)
    assert s.questions[1].show_if.values == [2, 3]
    assert s.questions[2].show_if is None
