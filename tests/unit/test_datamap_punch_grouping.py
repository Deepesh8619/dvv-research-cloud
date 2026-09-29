"""A multi-punch is one question over many columns, not many questions.

`materialize` WRITES this convention — "{qid}r{code}, Decipher multi-punch
naming so questionnaire<->data linking works for free" — and the linker reads
it. Recovery did not, and only grouped on a `question_ref` the adapter supplies
for some formats and not for Excel.

So a 22-option multi came back as 22 separate yes/no questions, each labelled
with one of its own OPTIONS. Measured on a real client file: 483 punch columns
arriving as 483 questions, from 46 real ones.
"""

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import QuestionType, SurveyDefinition
from src.engines.questionnaire.datamap_recovery import (
    questions_from_datamap,
    recover_missing,
)

_CHECKBOX = {0: "Unchecked", 1: "Checked"}


def _punches(stem: str, labels: list[str]) -> list[Variable]:
    return [
        Variable(name=f"{stem}r{i}", label=label, type=VarType.SINGLE, values=dict(_CHECKBOX))
        for i, label in enumerate(labels, start=1)
    ]


def _map(*variables: Variable) -> DataMap:
    return DataMap(variables=list(variables))


def test_punch_columns_become_one_question():
    datamap = _map(*_punches("Q2a", ["Bubble", "Anua", "Byoma"]))
    found = questions_from_datamap(datamap)
    assert [q.qid for q in found] == ["Q2a"]
    assert len(found[0].variables) == 3


def test_the_rebuilt_question_is_a_multi_with_the_column_labels_as_options():
    survey = SurveyDefinition(study_id="t")
    recovered, _links = recover_missing(
        survey, _map(*_punches("Q2a", ["Bubble", "Anua", "Byoma"]))
    )
    assert [q.qid for q in recovered] == ["Q2a"]
    question = recovered[0]
    assert question.type is QuestionType.MULTI
    assert [(o.code, o.label) for o in question.options] == [
        (1, "Bubble"), (2, "Anua"), (3, "Byoma"),
    ]


def test_a_grouped_question_never_takes_an_option_as_its_wording():
    """One column's label IS the question; several columns' labels are OPTIONS,
    and using the first would call this question "Bubble"."""
    survey = SurveyDefinition(study_id="t")
    recovered, _links = recover_missing(
        survey, _map(*_punches("Q2a", ["Bubble", "Anua"]))
    )
    assert recovered[0].text != "Bubble"


def test_a_lone_r1_column_is_not_evidence_of_a_multi():
    datamap = _map(
        Variable(name="Q9r1", label="Something", type=VarType.SINGLE, values=dict(_CHECKBOX))
    )
    assert [q.qid for q in questions_from_datamap(datamap)] == ["Q9r1"]


def test_a_file_holding_both_the_stem_and_its_punches_groups_neither():
    """`Q5` beside `Q5r1` is describing two different things, and collapsing
    them would put a question's own column inside itself."""
    datamap = _map(
        Variable(name="Q5", label="Which one?", type=VarType.SINGLE, values={1: "A", 2: "B"}),
        *_punches("Q5", ["A", "B"]),
    )
    assert sorted(q.qid for q in questions_from_datamap(datamap)) == ["Q5", "Q5r1", "Q5r2"]


def test_an_adapter_that_supplies_question_ref_still_wins():
    """A format that records the parent knows better than a naming convention."""
    variables = [
        Variable(name="odd_1", label="A", type=VarType.SINGLE, values=dict(_CHECKBOX),
                 question_ref="Q7"),
        Variable(name="odd_2", label="B", type=VarType.SINGLE, values=dict(_CHECKBOX),
                 question_ref="Q7"),
    ]
    assert [q.qid for q in questions_from_datamap(_map(*variables))] == ["Q7"]


def test_single_column_questions_are_untouched():
    datamap = _map(
        Variable(name="S1", label="What is your age?", type=VarType.SINGLE,
                 values={1: "18-34", 2: "35+"}),
    )
    found = questions_from_datamap(datamap)
    assert [q.qid for q in found] == ["S1"]
    assert found[0].text == "What is your age?"
