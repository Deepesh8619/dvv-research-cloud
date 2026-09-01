"""MR techniques as three real client questionnaires actually write them.

Every string below is taken verbatim from a client document. A mask that is
read wrongly is worse than one not read at all: it claims the question was
understood while pointing at the wrong source.
"""

from __future__ import annotations

import pytest

from src.engines.questionnaire.mr_techniques import detect_techniques

# --- Masking ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("directive", "mode", "source"),
    [
        ("[PN: ONLY DISPLAY OPTIONS SELECTED AT S6]", "selected", "S6"),
        ("[PN: SHOW ONLY ITEMS SELECTED AT Q18 IN ROWS. RANDOMIZE ROWS]", "selected", "Q18"),
        ("Which is most believable? [SHOW Q26 LIST. RANDOMIZE]", "full_list", "Q26"),
        ("Unaware (Not selected at Q2a)", "not_selected", "Q2A"),
        (
            "[PN: INSERT BRANDS USED WITHIN PAST 12 MONTHS FROM Q13 & Q13a.]",
            "selected",
            "Q13",
        ),
    ],
)
def test_masks_read_from_real_directives(directive, mode, source):
    masks = detect_techniques(directive).masks
    assert len(masks) == 1
    assert (masks[0].mode, masks[0].source_qid) == (mode, source)


def test_the_most_specific_mask_wins():
    """"SHOW Q26 LIST EXCEPT FOR Q27 SELECTION" also matches the plain
    "SHOW Q26 LIST" pattern; reading it as the plain form would show the
    respondent the option they just picked."""
    masks = detect_techniques("[SHOW Q26 LIST EXCEPT FOR Q27 SELECTION]").masks
    assert len(masks) == 1
    assert masks[0].mode == "list_except"
    assert masks[0].source_qid == "Q26"
    assert masks[0].exclude_qid == "Q27"


def test_inserting_one_response_is_a_pipe_not_a_mask():
    """"INSERT RESPONSE FROM Q8" puts an answer into the wording. Calling it a
    mask would claim Q17e's options come from Q8, which they do not."""
    t = detect_techniques("preferred pharmacy ([INSERT RESPONSE FROM Q8]) fills all types?")
    assert t.masks == []
    assert [p.source_qid for p in t.pipes] == ["Q8"]


# --- Piping ----------------------------------------------------------------


def test_a_pipe_that_names_a_question_is_resolvable():
    t = detect_techniques("You mentioned [INSERT Q7 #1 RANK] as your top choice.")
    assert [p.source_qid for p in t.pipes] == ["Q7"]


def test_a_pipe_that_names_a_scripter_variable_is_recorded_not_guessed():
    """"[INSERT PHARMACY]" refers to something the script computes. Inventing
    a source question for it would be a fabrication."""
    t = detect_techniques("What comes to mind when you think of [INSERT PHARMACY]?")
    assert [(p.source_qid, p.name) for p in t.pipes] == [(None, "PHARMACY")]


# --- Least fill ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "CODE UP TO 3 PRIORITY PHARMACIES – ASSIGN BASED ON LEAST FILL",
        "[PN: RESPONDENTS WILL RECEIVE 3 OPEN ENDS ON A ROTATED AND LEAST FILLED BASIS]",
        "assigned by least-fill",
    ],
)
def test_least_fill_is_recognised_however_it_is_written(text):
    assert detect_techniques(text).least_fill is True


def test_least_fill_is_not_claimed_when_absent():
    assert detect_techniques("[PN: RANDOMIZE]").least_fill is False


# --- Nothing is invented ---------------------------------------------------


def test_an_ordinary_question_declares_no_techniques():
    t = detect_techniques("Q4. How satisfied are you?", "[PN: RANDOMIZE]")
    assert not t
    assert t.to_dict() == {"masks": [], "pipes": [], "least_fill": False}


def test_the_same_mask_stated_twice_is_recorded_once():
    t = detect_techniques(
        "[PN: SHOW ONLY ITEMS SELECTED AT Q18 IN ROWS]",
        "[PN: SHOW ONLY ITEMS SELECTED AT Q18 IN COLUMNS]",
    )
    assert len(t.masks) == 1
