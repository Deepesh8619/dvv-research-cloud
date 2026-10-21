"""Follow-up probes: which answers are thin, what is asked, where it lands."""

import pytest

from src.core.survey_definition import Block, Question, QuestionType, SurveyDefinition
from src.engines.fielding import probes
from src.engines.fielding.materialize import materialize_fielding
from src.renderers.surveyjs_compiler import compile_to_surveyjs

# The same answers are in web/src/components/respondent/probes.test.ts.
THIN = ["Lower the rates", "Nothing comes to mind", "efficacy and safety", "ok", "Don't know",
        "N/A", "good"]
FULL = ["The rates went up twice this year and the leads did not follow, so we cut spend"]


@pytest.mark.parametrize("answer", THIN)
def test_thin(answer):
    assert probes.is_thin(answer)


@pytest.mark.parametrize("answer", [*FULL, "", "   "])
def test_not_thin(answer):
    assert not probes.is_thin(answer)


def test_the_model_writes_the_probe_unless_it_leads_or_is_unreachable(monkeypatch):
    from types import SimpleNamespace

    replies = iter(["What would make the rates feel fair to you?",
                    "Don't you agree the rates are too high?"])
    monkeypatch.setattr("src.ai.router.run_task",
                        lambda *a, **k: SimpleNamespace(content={"probe": next(replies)}))
    assert probes.write_probe("Why?", "Lower the rates") == (
        "What would make the rates feel fair to you?", "model")
    assert probes.write_probe("Why?", "Lower the rates") == (probes.FALLBACK, "fixed")

    def down(*a, **k):
        raise RuntimeError("no backend")
    monkeypatch.setattr("src.ai.router.run_task", down)
    assert probes.write_probe("Why?", "nothing") == (probes.FALLBACK_NOTHING, "fixed")


def test_a_probed_question_gets_a_follow_up_page_and_two_columns():
    q = Question(qid="Q5", text="Why did you cut spend?", type=QuestionType.OPEN_TEXT,
                 validation={"probe": True})
    s = SurveyDefinition(study_id="s", title="T", source="authored", status="approved", version=1,
                         blocks=[Block(block_id="B1", question_ids=["Q5"])], questions=[q])
    elements = compile_to_surveyjs(s)["pages"][0]["elements"]
    assert [e["name"] for e in elements] == ["Q5", "Q5_PROBE"]
    assert elements[1]["visibleIf"] == "isThin({Q5})" and not elements[1]["isRequired"]

    from types import SimpleNamespace
    sess = SimpleNamespace(id="a", status_code=10, started_at="2026-10-21T10:00:00",
                           completed_at="2026-10-21T10:05:00", entry_params={})
    dm, df = materialize_fielding(s, [sess], {"a": {
        "Q5": "Lower the rates", "Q5_PROBEQ": "What would make them fair?", "Q5_PROBE": "Half"}})
    assert df.loc[0, "Q5_PROBEQ"] == "What would make them fair?" and df.loc[0, "Q5_PROBE"] == "Half"
    assert dm["Q5_PROBE"].label == "Why did you cut spend? — follow-up answer"
