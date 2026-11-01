"""Count bands: "selected ONLY ONE of punch 2 at A, B, C" without a count operator."""

from src.ai.router import BackendUnavailable
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire.derived import derived_values
from src.engines.questionnaire.reading_questions import analyst_reading
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs

_OPTS = ["1. Alpha", "2. Beta", "3. Gamma"]
_LINES = [
    "T1. Which do you have at home? Select all.", *_OPTS,
    "T2. Which do you have at work? Select all.", *_OPTS,
    "T3. Which do you have on the road? Select all.", *_OPTS,
    "T4. Which do you have elsewhere? Select all.", "1. Alpha", "3. Gamma",
    "T5. Confirm.", "1. Yes", "2. No",
    "[PN: Create a variable called HOWMANY:]",
    "[PN: ONE THING = selected ONLY ONE of punch 2 at T1, T2 OR T3]",
    "[PN: ZERO THINGS = selected NONE of punch 2 at T1, T2, OR T3]",
    "[PN: THREE THINGS = selected ONLY THREE of punch 2 at T1, T2, T3]",
    "[PN: FOUR THINGS = N/A]",
    "[PN: LONE = selected punch 1 at T1 AND punch 1 at T2]",
]


def _extract(monkeypatch, lines):
    def off(*a, **k):
        raise BackendUnavailable("AI off")

    monkeypatch.setattr(ex, "run_task", off)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    blocks = [DocumentBlock(block_type="paragraph", text=t, paragraph_index=i)
              for i, t in enumerate(lines, 1)]
    return ex.extract_survey_definition(segment_into_questions(blocks), "t")


def _var(survey, name="HOWMANY"):
    return next(v for v in analyst_reading(survey)["variables"] if v["name"] == name)


def test_counts_land_in_the_right_band_from_a_real_document(monkeypatch):
    survey = _extract(monkeypatch, _LINES)
    var = _var(survey)
    assert var["status"] == "ready", var["why"]
    assert [b["label"] for b in var["bands"]] == ["ONE THING", "ZERO THINGS", "THREE THINGS",
                                                  "LONE"]  # "= N/A" is dropped, not an error
    compile_to_surveyjs(survey)
    q = Question(qid="HID_HOWMANY", text="how many", type=QuestionType.SINGLE,
                 options=[Option(code=i, label=b["label"],
                                 derive_if=Expression.from_dict(b["condition"]))
                          for i, b in enumerate(var["bands"], 1)])
    hid = SurveyDefinition(study_id="s", source="x", status="draft",
                           blocks=[Block(block_id="B", question_ids=["HID_HOWMANY"])],
                           questions=[q])

    def band(answers):
        return derived_values(hid, answers).get("HID_HOWMANY")

    assert band({"T1": [2], "T2": [1], "T3": [3]}) == 1          # punch 2 at exactly one
    assert band({"T1": [1, 3], "T2": [], "T3": [1]}) == 2        # at none
    assert band({"T1": [2], "T2": [2, 1], "T3": [2]}) == 3
    assert band({"T1": [2], "T2": [2], "T3": [1]}) is None       # two: no band
    assert all(c.op in {"and", "or", "not", "in"} for c in _walk(var["bands"][0]["condition"]))


def _walk(d):
    yield Expression.from_dict(d)
    for c in d.get("children") or []:
        yield from _walk(c)


def test_a_source_without_the_punch_is_skipped_and_said(monkeypatch):
    lines = [*_LINES[:-6], "[PN: Create a variable called HOWMANY:]",
             "[PN: ONE THING = selected ONLY ONE of punch 2 at T1, T2, T4]"]
    var = _var(_extract(monkeypatch, lines))
    assert var["status"] == "ready"
    band = var["bands"][0]
    assert "T4" in band["detail"] and band["condition"]["op"] == "or"
    assert Expression.from_dict(band["condition"]).referenced_qids() == {"T1", "T2"}
    assert evaluate(Expression.from_dict(band["condition"]), {"T1": [2], "T2": [1]})


def test_what_a_person_must_decide_is_named(monkeypatch):
    def why(rule):
        lines = [*_LINES[:18], "[PN: Create a variable called HOWMANY:]", f"[PN: ONE = {rule}]"]
        var = _var(_extract(monkeypatch, lines))
        assert var["status"] == "needs_you"
        return var["why"]

    assert "ZZ9" in why("selected ONLY ONE of punch 2 at T1, ZZ9")
    assert "punch 7" in why("selected ONLY ONE of punch 7 at T1, T2")
    assert "at least or exactly" in why("selected TWO of punch 2 at T1, T2, T3")
    assert "more than 8" in why("selected ONLY ONE of punch 2 at "
                                + ", ".join(["T1"] * 9))
    assert "only T1" in why("selected ONLY TWO of punch 2 at T1, T4")
