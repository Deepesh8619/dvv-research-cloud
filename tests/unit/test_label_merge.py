"""Label merge: approved questionnaire wording -> inferred datamap variables.

The fix for the 'raw codes instead of labels' gap on CSV/Excel imports. Tests
pin the conservative rules: only inferred provenance is touched, code
mismatches skip rather than force, SPSS labels are never overwritten.
"""

from __future__ import annotations

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.label_merge import merge_labels_into_datamap


def _survey() -> SurveyDefinition:
    q1 = Question(
        qid="S1",
        text="What is your gender?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male"), Option(code=2, label="Female")],
    )
    q2 = Question(
        qid="Q5",
        text="How satisfied are you overall?",
        type=QuestionType.SINGLE,
        options=[Option(code=i, label=lbl) for i, lbl in enumerate(
            ["Very dissatisfied", "Dissatisfied", "Neutral", "Satisfied", "Very satisfied"], 1
        )],
    )
    return SurveyDefinition(
        study_id="s1",
        title="t",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["S1", "Q5"])],
        questions=[q1, q2],
    )


def test_merge_applies_text_and_option_labels_to_inferred_vars():
    dm = DataMap()
    # Excel import: label = column header, values = raw codes.
    dm.add(Variable(name="S1", label="S1", type=VarType.SINGLE,
                    values={1: "1", 2: "2"}, provenance="excel_import"))
    dm.add(Variable(name="Q5", label="Q5", type=VarType.SINGLE,
                    values={1: "1", 2: "2", 3: "3", 4: "4", 5: "5"},
                    provenance="excel_import"))

    result = merge_labels_into_datamap(_survey(), dm)

    assert result.n_variables_relabelled == 2
    assert dm["S1"].label == "What is your gender?"
    assert dm["S1"].values == {1: "Male", 2: "Female"}
    assert dm["Q5"].values[5] == "Very satisfied"
    assert result.skipped_code_mismatch == []


def test_merge_never_touches_spss_provenance():
    dm = DataMap()
    dm.add(Variable(name="S1", label="Real SPSS label", type=VarType.SINGLE,
                    values={1: "Homme", 2: "Femme"}, provenance="spss"))
    result = merge_labels_into_datamap(_survey(), dm)
    assert dm["S1"].label == "Real SPSS label"
    assert dm["S1"].values == {1: "Homme", 2: "Femme"}
    assert result.n_variables_relabelled == 0


def test_merge_skips_on_code_mismatch_instead_of_forcing():
    dm = DataMap()
    # Data carries a code 98 ("prefer not to say" recode) the question lacks.
    dm.add(Variable(name="S1", label="S1", type=VarType.SINGLE,
                    values={1: "1", 2: "2", 98: "98"}, provenance="csv_import"))
    result = merge_labels_into_datamap(_survey(), dm)
    # Question text still applied (it's safe), but value labels left as codes.
    assert dm["S1"].label == "What is your gender?"
    assert dm["S1"].values == {1: "1", 2: "2", 98: "98"}
    assert "S1" in result.skipped_code_mismatch


def test_merge_reports_unmatched_questions():
    dm = DataMap()
    dm.add(Variable(name="TOTALLY_DIFFERENT", label="x", type=VarType.SINGLE,
                    values={1: "1"}, provenance="csv_import"))
    result = merge_labels_into_datamap(_survey(), dm)
    assert set(result.unmatched_questions) == {"S1", "Q5"}


def test_questionnaire_conditions_are_translated_to_the_scripts_codes():
    """TRD1: the document lists "Extremely" as 1, the script stored it as 3."""
    from src.core.survey_definition import Expression
    from src.engines.questionnaire.label_merge import (
        questionnaire_to_data_codes,
        translate_to_data_codes,
    )
    from src.engines.questionnaire.linker import LinkReport, QuestionVariableLink

    scale = ["Extremely", "Somewhat", "Not at all"]
    q9 = Question(qid="Q9", text="How familiar?", type=QuestionType.SINGLE,
                  options=[Option(code=i, label=lab) for i, lab in enumerate(scale, 1)])
    q10 = Question(qid="Q10", text="Why?", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="Yes")],
                   show_if=Expression(op="in", qid="Q9", values=[1, 2]))
    dm = DataMap(variables=[
        Variable(name="Q9", label="How familiar?", type=VarType.SINGLE, provenance="spss",
                 values={3: "Extremely", 2: "Somewhat", 1: "Not at all"}),
    ])
    survey = SurveyDefinition(study_id="s", questions=[q9, q10])
    links = LinkReport(links=[QuestionVariableLink(qid="Q9", variable_names=["Q9"])])
    mapping = questionnaire_to_data_codes(survey, dm, links)
    assert mapping == {"Q9": {1: 3, 2: 2, 3: 1}}
    translated = translate_to_data_codes(survey, mapping)
    assert sorted(translated["Q10"].show_if.values) == [2, 3]
    assert survey["Q10"].show_if.values == [1, 2]  # the original is untouched


def test_inferred_codes_are_never_translated():
    from src.engines.questionnaire.label_merge import questionnaire_to_data_codes
    from src.engines.questionnaire.linker import LinkReport, QuestionVariableLink

    q = Question(qid="Q9", text="x", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="A"), Option(code=2, label="B")])
    dm = DataMap(variables=[Variable(name="Q9", label="x", type=VarType.SINGLE,
                                     provenance="excel_import", values={2: "A", 1: "B"})])
    links = LinkReport(links=[QuestionVariableLink(qid="Q9", variable_names=["Q9"])])
    assert questionnaire_to_data_codes(SurveyDefinition(study_id="s", questions=[q]),
                                       dm, links) == {}


def test_other_specify_wordings_are_one_answer():
    from src.engines.questionnaire.label_merge import _norm_label

    assert _norm_label("Other (Specify)") == _norm_label("Other (please specify):")
    assert _norm_label("Other brand") != _norm_label("Other (Specify)")
