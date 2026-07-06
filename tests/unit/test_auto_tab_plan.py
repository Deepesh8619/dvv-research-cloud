"""auto_tab_plan() / suggest_banner_candidates() — a real client file surfaced
two related issues:

1. Decipher-style exports use hid_/dummy_/tt_ prefixes for BOTH true system
   paradata (device fingerprints, bot-detection flags) AND legitimate
   hand-recoded demographics (Hid_S1 = clean 3-category gender recode,
   TT_AGE_RANGES = clean age bands). The prefix alone can't tell them apart,
   so banner *candidacy* (_is_paradata_variable) is deliberately narrower than
   stub candidacy (_is_system_variable).

2. Even with candidacy fixed, auto-selecting "the first N qualifying
   variables in file order" reliably surfaced operational/QA variables
   (device type, OS, bot flags) ahead of real demographics, and silently
   stapled that unrelated banner onto every question asked in chat — e.g. a
   Country crosstab getting Vehicle-Purchase-Intent columns because that's
   what happened to auto-select. Product decision: auto_tab_plan() no longer
   picks a default banner at all (every stub is Total-only unless a banner is
   explicitly requested); suggest_banner_candidates() is kept as a tested,
   reusable building block for a human to choose from instead.
"""

from src.core.models import DataMap, Variable, VarType
from src.orchestration.orchestrator import (
    MAX_BANNER_LABEL_LEN,
    _is_binary_flag,
    _is_paradata_variable,
    _is_system_variable,
    auto_tab_plan,
    suggest_banner_candidates,
)


def _datamap(*variables: Variable) -> DataMap:
    return DataMap(list(variables))


def test_auto_tab_plan_never_selects_a_default_banner():
    """Product decision: every stub is Total-only unless a banner is
    explicitly requested (via a manual tab_plan.yaml or the chat navigator's
    show_table naming one) — auto_tab_plan must not guess one."""
    dm = _datamap(
        Variable("Q1", "Region", VarType.SINGLE, {1: "North", 2: "South"}),
        Variable("Q2", "Satisfaction", VarType.SINGLE, {1: "Low", 2: "High"}),
    )
    plan = auto_tab_plan(dm)
    assert plan is not None
    assert plan.banner == []
    # Both qualifying single-choice variables still become stub tables —
    # dropping default-banner selection doesn't remove them as candidates.
    stub_names = {t.stub for t in plan.tables}
    assert stub_names == {"Q1", "Q2"}


def test_hid_prefixed_clean_recode_qualifies_as_banner_candidate():
    """Hid_S1: a hand-recoded, short-labelled gender variable. Real-world
    example: excluded from banner candidacy entirely before this fix, purely
    because of its prefix, even though it's cleaner than the raw S1 question.
    """
    dm = _datamap(
        Variable(
            "Hid_S1", "Gender (recoded)", VarType.SINGLE, {1: "Female", 2: "Male", 3: "Other"}
        ),
    )
    assert not _is_paradata_variable("Hid_S1")
    assert _is_system_variable("Hid_S1")  # still excluded from stub candidacy

    assert suggest_banner_candidates(dm) == ["Hid_S1"]


def test_tt_prefixed_age_bands_qualify_as_banner_candidate():
    """TT_AGE_RANGES: clean 6-band age recode, real-world example that was
    excluded purely by its tt_ prefix before this fix."""
    dm = _datamap(
        Variable(
            "TT_AGE_RANGES",
            "Age band",
            VarType.SINGLE,
            {1: "18-24", 2: "25-34", 3: "35-44", 4: "45-54", 5: "55-64", 6: "65-70"},
        ),
    )
    assert suggest_banner_candidates(dm) == ["TT_AGE_RANGES"]


def test_true_paradata_excluded_from_banner_and_stub():
    """record/uuid/vendorid and camelCase device fingerprints are junk under
    any convention — must stay excluded from both pools regardless of the
    relaxed hid_/dummy_/tt_ handling."""
    for name in ("record", "uuid", "vendorid", "dTrack"):
        assert _is_paradata_variable(name), f"{name} should still be paradata"
        assert _is_system_variable(name), f"{name} should still be a system variable"


def test_long_labels_still_excluded_from_banner_candidacy():
    """REGIONCODE-style variables (real-world: 83-char region descriptions)
    must stay excluded from banner candidacy — labels that long don't fit a
    column header, regardless of prefix or category count."""
    long_label = "A" * (MAX_BANNER_LABEL_LEN + 1)
    dm = _datamap(
        Variable("REGIONCODE", "Region", VarType.SINGLE, {1: long_label, 2: "Short"}),
    )
    assert suggest_banner_candidates(dm) == []


def test_binary_flag_excluded_from_banner_candidacy_regardless_of_prefix():
    """A hid_/dummy_-prefixed 0/1 multi-punch flag must still be excluded —
    the paradata-vs-recode distinction doesn't override the binary-flag
    check, which catches "0 (A) 1 (B)" junk columns specifically."""
    dm = _datamap(
        Variable("Hid_C2br1", "Multi-punch flag", VarType.SINGLE, {0: "0", 1: "1"}),
    )
    assert _is_binary_flag(dm["Hid_C2br1"])
    assert suggest_banner_candidates(dm) == []


def test_paradata_check_is_narrower_than_system_check():
    """The whole point of the fix: _is_paradata_variable (banner) must accept
    strictly more names than _is_system_variable (stub) rejects — every
    hid_/h_/hcf/dummy_-prefixed name that's paradata-excluded should NOT be
    paradata under the narrower check, unless it's also in the always-junk
    set (record/uuid/etc. or noanswer/geoinfor/respinfor prefixes)."""
    recode_names = ["Hid_S1", "hcf_income_band", "dummy_race_group", "h_vmobileos"]
    for name in recode_names:
        assert _is_system_variable(name), f"{name} should be excluded from stub candidacy"
        assert not _is_paradata_variable(name), f"{name} should NOT be excluded from banner"


def test_suggest_banner_candidates_respects_limit():
    dm = _datamap(
        Variable("V1", "One", VarType.SINGLE, {1: "A", 2: "B"}),
        Variable("V2", "Two", VarType.SINGLE, {1: "A", 2: "B"}),
        Variable("V3", "Three", VarType.SINGLE, {1: "A", 2: "B"}),
        Variable("V4", "Four", VarType.SINGLE, {1: "A", 2: "B"}),
    )
    assert suggest_banner_candidates(dm, limit=2) == ["V1", "V2"]
