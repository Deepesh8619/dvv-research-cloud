"""Questionnaire format-profile detection (deterministic, no LLM).

Hand-built block lists exercise each recognized convention plus the unknown
fallback, and the registry-extensibility contract (a new profile is picked up by
the detector without editing detector code).
"""

from __future__ import annotations

from src.engines.questionnaire import profiles
from src.engines.questionnaire.profiles import (
    QuestionnaireProfile,
    detect_profile,
)
from src.intake.questionnaire_loader import DocumentBlock


def _para(text: str) -> DocumentBlock:
    return DocumentBlock(block_type="paragraph", text=text, paragraph_index=0)


def _qname_table(token: str) -> DocumentBlock:
    # A table block whose serialized text carries the `Q Name` row (the loader's
    # GBK convention). rows are present but the detector reads _table_qid.
    return DocumentBlock(
        block_type="table",
        text=f"Q Name: {token}\nResponse list:\n1: Yes\n2: No",
        paragraph_index=0,
        rows=[["Q Name", token], ["Response list:", ""]],
    )


def test_gbk_spec_table_detected_high_confidence():
    blocks = [_qname_table("RESP_AGE"), _qname_table("SS_EMP_STATUS"), _qname_table("Q3")]
    d = detect_profile(blocks)
    assert d.profile.slug == "gbk_spec_table"
    assert d.confidence >= 0.9
    assert d.signals["qname_tables"] == 3
    assert d.profile.prompt_hint == profiles.HINT_FIELD_SPEC


def test_prose_labelled_detected():
    blocks = [
        _para("Q5. Are you aware of Brand X?"),
        _para("S3. How old are you?"),
        _para("P1. Do you have children?"),
    ]
    d = detect_profile(blocks)
    assert d.profile.slug == "prose_labelled"
    assert d.confidence >= 0.9
    assert d.profile.prompt_hint == profiles.HINT_PROSE


def test_numbered_plain_detected_and_shares_prose_hint():
    blocks = [_para("1. Are you aware of Brand X?"), _para("2. How old are you?")]
    d = detect_profile(blocks)
    assert d.profile.slug == "numbered_plain"
    # Bare-number handling is identical to prose: same prompt hint, no spec guidance.
    assert d.profile.prompt_hint == profiles.HINT_PROSE


def test_unknown_falls_back_low_confidence():
    blocks = [
        _para("Thank you for agreeing to take part in this survey."),
        _para("Your answers are confidential. Please continue."),
    ]
    d = detect_profile(blocks)
    assert d.profile.slug == "unknown"
    assert d.confidence < profiles._CONFIDENCE_FLOOR
    assert d.profile.prompt_hint == profiles.HINT_CONSERVATIVE


def test_empty_document_is_unknown():
    d = detect_profile([])
    assert d.profile.slug == "unknown"


def test_dominant_format_wins_in_mixed_document():
    # Mostly GBK tables with one stray prose label -> gbk still wins on share.
    blocks = [_qname_table("A"), _qname_table("B"), _qname_table("C"), _para("Q9. stray")]
    d = detect_profile(blocks)
    assert d.profile.slug == "gbk_spec_table"


def test_new_profile_is_picked_up_without_touching_detector(monkeypatch):
    """Extensibility contract: appending a profile to REGISTRY is enough.

    We register a profile that scores 1.0 whenever any bold heading is present.
    detect_profile must pick it with NO change to detect_profile itself.
    """
    def heading_scorer(stats):
        # Fire on documents dominated by our custom signal; reuse n_blocks as a
        # stand-in "all blocks are our format" measure for the test doc below.
        conf = 1.0 if stats.n_blocks and stats.question_ish == 0 else 0.0
        return conf, {"custom": stats.n_blocks}

    custom = QuestionnaireProfile(
        slug="acme_custom",
        label="ACME custom format",
        prompt_hint=profiles.HINT_PROSE,
        scorer=heading_scorer,
    )
    monkeypatch.setattr(profiles, "REGISTRY", [*profiles.REGISTRY, custom])

    # A doc with no standard signal (question_ish == 0) that our custom scorer claims.
    blocks = [_para("Some ACME-formatted content"), _para("More content")]
    d = detect_profile(blocks)
    assert d.profile.slug == "acme_custom"
    assert d.confidence == 1.0


def test_demo_self_check_runs():
    profiles._demo()  # asserts internally
