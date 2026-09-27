"""The derivation of a hidden variable is written in the document, in a column.

A spec table carries `Label: | Definition:` over a hidden variable's codes, and
the definition cell is how that value is produced: "HID_GEO_DIVISION = 1,2". It
was being dropped, so the one place the rule is written down reached nobody —
not the reviewer, not the workbook, not the person who has to script it.

Kept verbatim and never executed. Of 36 definition cells in one real document
only 10 are a plain "VAR = codes"; the rest say things like "SS_CHANNELS = 2
and/or 4 ONLY (no others selected)" and "NOT Google Ads Current User AND ...".
A rule compiled half-right puts numbers in a banner variable nobody authored.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.definition_audit import audit_definition
from src.engines.questionnaire.spec_table import parse_spec_table
from src.intake.questionnaire_loader import DocumentBlock

_HEADER = ["", "Label:", "Label:", "Definition:"]


def _spec(*option_rows):
    rows = [
        ["Q Name", "HID_GEO_REGION", "HID_GEO_REGION", "HID_GEO_REGION"],
        _HEADER,
        *option_rows,
        ["Q type", "Std Single-Response (SR)", "", ""],
        ["Show / HV", "Hidden variable", "", ""],
    ]
    return parse_spec_table(DocumentBlock("table", "", 0, rows=rows))


def test_the_definition_column_is_read_onto_the_option():
    fields = _spec(
        ["1", "Northeast", "Northeast", "HID_GEO_DIVISION = 1,2"],
        ["2", "Midwest", "Midwest", "HID_GEO_DIVISION = 3,4"],
    )
    assert [(o.code, o.label, o.definition) for o in fields.options] == [
        (1, "Northeast", "HID_GEO_DIVISION = 1,2"),
        (2, "Midwest", "HID_GEO_DIVISION = 3,4"),
    ]


def test_a_table_with_no_definition_column_gives_no_derivations():
    """A merged label cell repeats across columns. Reading it as a definition
    would give every option a "derivation" that is only its own name."""
    rows = [
        ["Q Name", "S1", "S1", "S1"],
        ["", "Label:", "Label:", "Label:"],
        ["1", "Yes", "Yes", "Yes"],
        ["2", "No", "No", "No"],
    ]
    fields = parse_spec_table(DocumentBlock("table", "", 0, rows=rows))
    assert all(o.definition is None for o in fields.options)


def _survey(*options):
    question = Question(
        qid="HID_GEO_REGION", text="Region", type=QuestionType.INFO,
        options=list(options), flags=["DERIVED_VARIABLE"],
    )
    return SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="b", question_ids=["HID_GEO_REGION"])],
        questions=[question],
    )


def test_two_values_with_the_same_derivation_are_reported():
    """Found in a real client document: HID_GEO_REGION gives Northeast and
    South the identical rule. Whoever scripts it has to pick, and whichever
    they pick the other region is simply absent from the data."""
    survey = _survey(
        Option(code=1, label="Northeast", definition="HID_GEO_DIVISION = 1,2"),
        Option(code=2, label="Midwest", definition="HID_GEO_DIVISION = 3,4"),
        Option(code=3, label="South", definition="HID_GEO_DIVISION = 1,2"),
    )
    finding = next(
        f for f in audit_definition(survey) if f.check == "derivation_defined_twice"
    )
    assert "Northeast" in finding.detail and "South" in finding.detail
    assert "Midwest" not in finding.detail


def test_one_pointer_repeated_across_every_value_is_not_a_clash():
    """"Based on C_ZIP" against all nine census divisions is a pointer for the
    whole variable, not nine rules that happen to agree — and reporting it
    would fire on every well-written variable of that shape."""
    survey = _survey(*[
        Option(code=i, label=f"Division {i}", definition="Based on C_ZIP")
        for i in range(1, 10)
    ])
    assert not [
        f for f in audit_definition(survey) if f.check == "derivation_defined_twice"
    ]


def test_a_questionnaire_with_no_derivations_is_quiet():
    survey = _survey(Option(code=1, label="Yes"), Option(code=2, label="No"))
    assert not [
        f for f in audit_definition(survey) if f.check == "derivation_defined_twice"
    ]
