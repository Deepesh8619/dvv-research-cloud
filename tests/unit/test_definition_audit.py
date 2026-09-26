"""The audit must name a stale definition — and stay quiet on a clean one."""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.definition_audit import (
    audit_all,
    audit_definition,
    format_report,
)


def _survey(*questions: Question, metadata: dict | None = None) -> SurveyDefinition:
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )
    survey.metadata.update(metadata or {})
    return survey


def _checks(survey) -> set[str]:
    return {f.check for f in audit_definition(survey)}


def test_a_clean_definition_reports_nothing():
    survey = _survey(
        Question(
            qid="Q1",
            text="Favourite colour?",
            options=[Option(code=1, label="Red"), Option(code=2, label="Blue")],
        )
    )
    assert audit_definition(survey) == []
    assert "0 affected" in format_report(audit_all([]))


def test_prose_stored_as_an_option_is_caught():
    survey = _survey(
        Question(
            qid="P1",
            text="Are you a parent?",
            options=[
                Option(code=1, label="Yes"),
                Option(code=2, label="No"),
                Option(
                    code=3,
                    label=(
                        "In this survey, we're looking to interview young women ages 13 "
                        "to 17 about their skincare habits, and we would like your "
                        "permission to speak with your teenager."
                    ),
                ),
            ],
        )
    )
    assert "prose_as_option" in _checks(survey)


def test_a_scripter_tag_left_in_a_label_is_caught():
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            type=QuestionType.MULTI,
            options=[
                Option(code=1, label="Ulta"),
                Option(code=2, label="None of the above [ANCHOR] [EXCLUSIVE]"),
            ],
        )
    )
    assert "scripter_tag_in_label" in _checks(survey)


def test_an_unapplied_recruitment_path_is_caught():
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            options=[Option(code=1, label="Ulta")],
            source_text="S6. Which retailers?\nParent Path (IF S0=1 AND S1=26-60) ASK P1-P3",
        ),
        Question(qid="P1", text="Parent?", options=[Option(code=1, label="Yes")]),
    )
    assert "section_path_unapplied" in _checks(survey)


def test_a_path_the_current_parser_applied_is_not_reported():
    """Same header, but `section_paths` metadata proves this pass already ran."""
    survey = _survey(
        Question(
            qid="S6",
            text="Which retailers?",
            options=[Option(code=1, label="Ulta")],
            source_text="S6. Which retailers?\nParent Path (IF S0=1 AND S1=26-60) ASK P1-P3",
        ),
        Question(
            qid="P1",
            text="Parent?",
            options=[Option(code=1, label="Yes")],
            show_if=Expression(op="in", qid="S0", values=[1]),
        ),
        metadata={"section_paths": [{"path": "Parent Path", "questions": ["P1"]}]},
    )
    assert "section_path_unapplied" not in _checks(survey)


def test_exclusive_on_a_single_select_is_caught():
    survey = _survey(
        Question(
            qid="Q4",
            text="Which brand?",
            type=QuestionType.SINGLE,
            options=[
                Option(code=1, label="Ulta"),
                Option(code=2, label="None", exclusive=True),
            ],
        )
    )
    assert "exclusive_on_single" in _checks(survey)


def test_a_self_referential_base_is_caught():
    survey = _survey(
        Question(
            qid="P1",
            text="Parent?",
            options=[Option(code=1, label="Yes")],
            show_if=Expression(op="in", qid="P1", values=[1]),
        )
    )
    assert "self_referential_base" in _checks(survey)


def test_a_grid_with_no_rows_is_caught():
    """It emits NO data columns — and used to pass validation on the strength of
    `options`, which a grid cannot use. One real question was in exactly this
    state: typed correctly, 12 options, delivering nothing."""
    from src.engines.questionnaire import schema
    from src.engines.questionnaire.extractor import ExtractionResult, _validate_question

    q = Question(
        qid="Q6",
        text="How often do you buy each category?",
        type=QuestionType.GRID_SINGLE,
        options=[Option(code=i, label=f"Category {i}") for i in range(1, 13)],
    )
    result = ExtractionResult(question=q, confidence={})
    _validate_question(result, {})
    assert schema.FLAG_EMPTY_OPTIONS in result.flags

    q.rows = [Option(code=1, label="Cleansers")]
    q.cols = [Option(code=1, label="Often"), Option(code=2, label="Never")]
    ok = ExtractionResult(question=q, confidence={})
    _validate_question(ok, {})
    assert schema.FLAG_EMPTY_OPTIONS not in ok.flags


def test_a_question_asked_about_each_thing_that_kept_only_one_answer_is_found():
    """From the live pharmacy study: "How familiar are you with each of these
    pharmacies?" came back as a single-choice question whose options were the
    familiarity scale. The pharmacies were gone — no column for any of them,
    and a respondent asked to rate thirteen with one click.

    Nothing in the instrument says this is wrong: the question has a type,
    options and a valid data column. Only the wording gives it away."""
    flattened = Question(
        qid="Q5", text="How familiar are you with each of these pharmacies?",
        type=QuestionType.SINGLE,
        options=[Option(code=i, label=label) for i, label in enumerate(
            ["I know a little", "I know some things", "I know them well"], start=1)],
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q5"])], questions=[flattened],
    )
    assert [f.check for f in audit_definition(survey)] == ["per_item_question_flattened"]


def test_a_grid_that_kept_its_items_is_not_flagged():
    kept = Question(
        qid="Q13", text="When did you last use each of these pharmacies?",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        cols=[Option(code=1, label="This month"), Option(code=2, label="Longer ago")],
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q13"])], questions=[kept],
    )
    assert audit_definition(survey) == []


def test_a_mask_pointing_at_a_yes_no_question_cannot_be_narrowing_a_brand_list():
    """Also live: thirteen pharmacy rows masked against a two-answer question.
    Whatever that mask is doing, it is not what it says — and the reason is
    that the source USED to be a per-pharmacy grid before extraction
    flattened it."""
    yes_no = Question(
        qid="Q4a", text="Have you used these pharmacies?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    recency = Question(
        qid="Q13", text="When did you last use each one?", type=QuestionType.GRID_SINGLE,
        rows=[Option(code=i, label=f"Pharmacy {i}") for i in range(1, 14)],
        cols=[Option(code=1, label="Recently"), Option(code=2, label="Longer ago")],
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q4a"}]}},
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q4a", "Q13"])], questions=[yes_no, recency],
    )
    assert "mask_cannot_narrow" in [f.check for f in audit_definition(survey)]


def test_a_mask_against_a_list_of_the_same_size_is_left_alone():
    aware = Question(
        qid="Q3", text="Which of these pharmacies have you heard of?", type=QuestionType.MULTI,
        options=[Option(code=i, label=f"Pharmacy {i}") for i in range(1, 11)],
    )
    used = Question(
        qid="Q4", text="Which have you used?", type=QuestionType.MULTI,
        options=[Option(code=i, label=f"Pharmacy {i}") for i in range(1, 11)],
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q3"}]}},
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q3", "Q4"])], questions=[aware, used],
    )
    assert "mask_cannot_narrow" not in [f.check for f in audit_definition(survey)]


def test_a_base_tag_naming_a_subgroup_is_reported_but_base_all_is_not():
    """The unnumbered questionnaires state every base as "[BASE: ...]".

    Both existing base checks need a qid to fire, and these tags name no
    question at all, so a question the document plainly restricts went to
    everybody with nothing said about it — the silent kind of failure. The
    finding is a SUBGROUP base; "[BASE: ALL]" is the absence of a condition and
    must stay quiet, or every question in the document reports itself.
    """
    subgroup = Question(
        qid="Q3",
        text="What drives your choice?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Efficacy")],
        source_text="[BASE: THOSE WHO USE SPRAVATO] What drives your choice?",
    )
    everyone = Question(
        qid="Q2",
        text="How satisfied are you?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Very satisfied")],
        source_text="[BASE: ALL] How satisfied are you?",
    )
    findings = {
        f.check: f.qids for f in audit_definition(_survey(everyone, subgroup))
    }
    assert findings.get("base_stated_in_words_only") == ["Q3"]
