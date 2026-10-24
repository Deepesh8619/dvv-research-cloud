"""Every template and module builds a questionnaire that validates and compiles."""

import pytest

from src.core.survey_definition import Block, SurveyDefinition
from src.engines.fielding.maxdiff_screens import problems
from src.engines.questionnaire.library import MODULES, TEMPLATES, build_module, build_template
from src.renderers.surveyjs_compiler import compile_to_surveyjs

INPUTS = {"organisation": "DVV", "purpose": "p", "contact": "c@x.in", "category": "coffee",
          "brands": "A\nB\nC", "subject": "A", "attributes": "Taste\nPrice",
          "concept": "A can of cold coffee", "product": "the can", "currency": "₹",
          "items": "\n".join(f"Item {i}" for i in range(1, 13)), "question": "Why?",
          "specialties": "Psychiatrist\nGP"}


def _valid(questions):
    s = SurveyDefinition(study_id="s", title="T", source="authored", status="draft", version=1,
                         blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
                         questions=questions)
    assert s.validate() == []
    compile_to_surveyjs(s)
    assert all(not problems(q) for q in questions)


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_templates(template):
    _valid(build_template(template, INPUTS, set()))


@pytest.mark.parametrize("module", list(MODULES))
def test_modules(module):
    _valid(build_module(module, INPUTS, set()))


def test_scales_are_balanced_and_maxdiff_needs_enough_items():
    sat = build_module("satisfaction", INPUTS, set())[0]
    assert [o.label for o in sat.options][2] == "Neither satisfied nor dissatisfied"
    with pytest.raises(ValueError, match="between 6 and 30"):
        build_module("maxdiff", {**INPUTS, "items": "A\nB"}, set())


def test_awareness_asks_about_known_brands_only_even_when_renumbered():
    from src.renderers.surveyjs_compiler import compile_to_surveyjs

    qs = build_module("awareness", INPUTS, {"AWR_AIDED"})
    s = SurveyDefinition(study_id="s", title="T", source="authored", status="draft", version=1,
                         blocks=[Block(block_id="B1", question_ids=[q.qid for q in qs])],
                         questions=qs)
    by_name = {e["name"]: e for e in compile_to_surveyjs(s)["pages"][0]["elements"]}
    for qid in ("AWR_CONSIDER", "AWR_USED", "AWR_PREFER"):
        assert by_name[qid]["choicesVisibleIf"] == (
            "({AWR_AIDED_2} contains {item}) or {item} anyof [98]")
