"""Every column in a survey export plays a role; only some belong in the qnre.

The point of this module is to stop a reviewer chasing 400 variables that are
correctly absent from the questionnaire. Names below are taken verbatim from a
real 750-respondent Decipher export.
"""

from __future__ import annotations

import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.variable_roles import (
    EXPECTED_IN_QUESTIONNAIRE,
    classify_variable,
    summarize,
    unexplained,
)


@pytest.mark.parametrize(
    ("name", "label", "parent", "expected"),
    [
        ("RESP_AGE", "What is your age?", None, "question"),
        ("SS_RESPr1", "Digital advertising", "SS_RESP", "punch"),
        ("DA_GOALSr9", "Convert high-intent prospects", None, "punch"),
        ("SS_INDUSTRYr98oe", "Other, specify", None, "other_specify"),
        ("noanswerRESP_AGE_r99", "", None, "no_answer"),
        ("HID_COMP_SIZE", "Hidden variable for company size", None, "hidden"),
        ("HidDevices", "Hidden question to punch device", None, "hidden"),
        ("h_vmobileos", "Hidden question to punch OS.", None, "hidden"),
        ("Hid_Segment", "Hidden: To punch the Segment", None, "hidden"),
        ("MAXDIFF_LIKELY_MD_1best", "", None, "maxdiff"),
        ("MAXDIFF_LIKELY_MD_12worst", "", None, "maxdiff"),
        ("MD_Items_1", "Items", None, "maxdiff"),
        ("SLMAXDIFFLEASTA", "VIRTUAL VARIABLE: FLAG IF ...", None, "qa_flag"),
        ("record", "Record number", None, "system"),
        ("uuid", "Participant identifier", None, "system"),
    ],
)
def test_real_export_columns_are_classified(name, label, parent, expected):
    assert classify_variable(name, label, parent) == expected


def test_maxdiff_beats_punch_and_question():
    """MD_Items_1 is punch-shaped and MAXDIFF..._1best is question-shaped;
    both are MaxDiff machinery and must not be mistaken for answers."""
    assert classify_variable("MD_Items_1") == "maxdiff"
    assert classify_variable("MAXDIFF_LIKELY_MD_1best") == "maxdiff"


def test_a_qa_flag_is_recognised_from_its_label_not_its_name():
    """SLMAXDIFFLEASTA says nothing; its label says VIRTUAL VARIABLE."""
    assert classify_variable("SLMAXDIFFMOSTA", "VIRTUAL VARIABLE: FLAG IF ...") == "qa_flag"


def test_only_questions_and_punches_are_expected_in_a_questionnaire():
    """A hidden variable is computed, not asked. Reporting it as a gap sends
    someone looking for a question that was never meant to exist."""
    assert EXPECTED_IN_QUESTIONNAIRE == {"question", "punch"}


def _datamap(rows):
    dm = DataMap()
    for name, label, parent in rows:
        dm.add(
            Variable(
                name=name, label=label, type=VarType.SINGLE, question_ref=parent
            )
        )
    return dm


def test_unexplained_lists_only_what_is_worth_chasing():
    dm = _datamap([
        ("RESP_AGE", "What is your age?", None),      # question, unmatched -> report
        ("SS_RESPr1", "Digital advertising", "SS_RESP"),  # punch, matched   -> no
        ("HID_COMP_SIZE", "Hidden variable", None),   # hidden              -> never
        ("MD_Items_1", "Items", None),                # maxdiff             -> never
        ("record", "Record number", None),            # system              -> never
    ])
    assert unexplained(dm, matched_variables={"SS_RESPr1"}) == ["RESP_AGE"]


def test_summarize_counts_every_variable_exactly_once():
    dm = _datamap([
        ("RESP_AGE", "What is your age?", None),
        ("SS_RESPr1", "Digital advertising", "SS_RESP"),
        ("HID_COMP_SIZE", "Hidden variable", None),
        ("record", "Record number", None),
    ])
    summary = summarize(dm)
    assert sum(summary.counts().values()) == 4
    assert summary.of("hidden") == ["HID_COMP_SIZE"]
    assert summary.counts()["question"] == 1


# --- Companions -------------------------------------------------------------
# An open question rarely stands alone: the script derives bands, thresholds and
# refusal flags from it, and the questionnaire's conditions are written against
# THOSE. Names taken verbatim from the real export.


def _companion_map():
    dm = DataMap()
    dm.add(Variable(name="SS_AD_SPEND", label="How much do you spend?", type=VarType.NUMERIC))
    dm.add(Variable(name="HID_SS_AD_SPEND",
                    label="Hidden variable: Flag if greater than $1,000,000", type=VarType.SINGLE))
    dm.add(Variable(name="noanswerSS_AD_SPEND_r99",
                    label="How much do you spend?: Prefer not to answer", type=VarType.SINGLE))
    dm.add(Variable(name="C_GEO_ZIP", label="What ZIP code?", type=VarType.TEXT))
    dm.add(Variable(name="hC_GEO_ZIP_Flagged",
                    label="Dummy variable to flag if respondent invalid US zip code",
                    type=VarType.SINGLE))
    dm.add(Variable(name="Hid_Segment", label="Hidden: To punch the Segment", type=VarType.SINGLE))
    return dm


def test_a_derived_variable_is_recognised_from_its_label_not_its_name():
    """`hC_GEO_ZIP_Flagged` is just "h" plus the question — the name prefix
    rules miss it, but its label says "Dummy variable to flag"."""
    assert classify_variable(
        "hC_GEO_ZIP_Flagged", "Dummy variable to flag if respondent invalid US zip code"
    ) == "hidden"


def test_companions_attach_to_the_question_they_derive_from():
    from src.engines.qc.variable_roles import find_companions

    companions = find_companions(_companion_map())
    assert {c.name for c in companions["SS_AD_SPEND"]} == {
        "HID_SS_AD_SPEND", "noanswerSS_AD_SPEND_r99"
    }
    assert {c.name for c in companions["C_GEO_ZIP"]} == {"hC_GEO_ZIP_Flagged"}


def test_a_study_level_hidden_variable_is_not_given_an_invented_parent():
    """`Hid_Segment` belongs to the study, not to any one question."""
    from src.engines.qc.variable_roles import find_companions

    companions = find_companions(_companion_map())
    assert not any(
        c.name == "Hid_Segment" for group in companions.values() for c in group
    )


def test_the_longest_matching_question_wins():
    """`HID_SS_AD_SPEND` must attach to `SS_AD_SPEND`, not to a shorter name
    that happens to be a substring of it."""
    from src.engines.qc.variable_roles import find_companions

    dm = _companion_map()
    dm.add(Variable(name="SS_AD", label="Do you advertise?", type=VarType.SINGLE))
    companions = find_companions(dm)
    assert {c.name for c in companions["SS_AD_SPEND"]} == {
        "HID_SS_AD_SPEND", "noanswerSS_AD_SPEND_r99"
    }
    assert "SS_AD" not in companions
