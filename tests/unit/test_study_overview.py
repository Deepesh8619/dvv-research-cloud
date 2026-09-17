"""The study overview: read it where it exists, cost nothing where it does not.

The quota table is the one table in a questionnaire where a typo costs money —
over-filling a cell means paying for sample the client will not accept — and it
was being retyped by hand because the chunk carries no question label.
"""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.definition_audit import audit_definition
from src.engines.questionnaire.study_overview import (
    looks_like_overview,
    parse_overview,
    unenforced_requirements,
)
from src.intake.questionnaire_loader import DocumentBlock


def _p(text: str, i: int) -> DocumentBlock:
    return DocumentBlock(block_type="paragraph", text=text, paragraph_index=i)


def _overview_blocks() -> list[DocumentBlock]:
    lines = [
        "Acme: Category Opportunity",
        "Study Overview",
        "RESEARCH OBJECTIVES:",
        "Understand where the category is growing.",
        "RESEARCH METHODOLOGY:",
        "Markets: United States",
        "Sample Size: 750 respondents",
        "LOI: 12 minutes",
        "SCREENING REQUIREMENTS:",
        "Age 25+",
        "Company size between 10 and 999 employees",
        "Decision maker or influences advertising decisions",
        "QUOTAS",
    ]
    blocks = [_p(text, i) for i, text in enumerate(lines, start=1)]
    blocks.append(
        DocumentBlock(
            block_type="table",
            text="quota table",
            paragraph_index=len(lines) + 1,
            rows=[
                ["Quota Groups", "Definition", "Target N", "Min N", "Max N"],
                ["Total N", "", "750", "", ""],
                ["Micro", "2-9 Employees", "", "75", "125"],
                ["Small", "10-99 Employees", "", "100", "250"],
            ],
        )
    )
    blocks.append(_p("General Programming Notes", len(lines) + 2))
    blocks.append(_p("Each question appears on a new screen", len(lines) + 3))
    return blocks


def test_the_brief_is_read_from_the_document():
    overview = parse_overview(_overview_blocks())
    assert overview.markets == "United States"
    assert overview.sample_size == 750
    assert overview.loi_minutes == 12
    assert overview.screening_requirements[0] == "Age 25+"
    assert len(overview.screening_requirements) == 3


def test_the_quota_table_is_read_with_its_limits():
    quotas = parse_overview(_overview_blocks()).quotas
    assert [q.group for q in quotas] == ["Total N", "Micro", "Small"]
    micro = quotas[1]
    assert (micro.definition, micro.min_n, micro.max_n) == ("2-9 Employees", 75, 125)
    assert quotas[0].target_n == 750
    # The header row names columns; it is not a quota.
    assert all(q.group != "Quota Groups" for q in quotas)


def test_a_questionnaire_without_an_overview_costs_nothing():
    """Most client questionnaires open at the first question. Absence is normal,
    and must not read as a defect — a flag here would mark every one of them."""
    blocks = [
        _p("S1. What is your age?", 1),
        _p("[Num box 18-99]", 2),
        _p("S2. Which of these do you use?", 3),
    ]
    assert looks_like_overview(blocks) is False
    overview = parse_overview(blocks)
    assert overview.is_empty()
    assert overview.quotas == []


def test_a_requirement_nothing_screens_on_is_reported():
    overview = parse_overview(_overview_blocks())
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["S1"])],
        questions=[
            Question(
                qid="S1",
                text="What is your age?",
                type=QuestionType.NUMERIC,
                terminate_if=Expression(op="lt", qid="S1", value=25),
            )
        ],
    )
    unmet = unenforced_requirements(overview, survey)
    assert "Age 25+" not in unmet  # the terminate screens on 25
    assert any("10 and 999 employees" in r for r in unmet)


def test_the_audit_reports_it_only_when_there_is_a_brief():
    question = Question(
        qid="S1", text="What is your age?", type=QuestionType.NUMERIC
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="draft",
        blocks=[Block(block_id="B1", question_ids=["S1"])], questions=[question],
    )
    assert "screening_requirement_not_enforced" not in {
        f.check for f in audit_definition(survey)
    }

    survey.metadata["study_overview"] = {"screening_requirements": ["Age 25+"]}
    finding = next(
        f for f in audit_definition(survey)
        if f.check == "screening_requirement_not_enforced"
    )
    assert "Age 25+" in finding.detail


def test_a_requirement_with_no_number_is_never_reported():
    """"Decision maker or influences advertising decisions" is enforced by a
    question's wording, not by a number. Reporting it every time would teach a
    reviewer to skip the check."""
    overview = parse_overview(_overview_blocks())
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="draft",
        blocks=[Block(block_id="B1", question_ids=["S1"])],
        questions=[Question(qid="S1", text="Role?", options=[Option(code=1, label="DM")])],
    )
    assert not any("Decision maker" in r for r in unenforced_requirements(overview, survey))
