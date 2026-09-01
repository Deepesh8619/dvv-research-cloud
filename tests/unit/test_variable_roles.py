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
