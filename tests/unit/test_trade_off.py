"""A trade-off exercise is named and refused — and an ordinary question is not.

The second half of that sentence is the one worth testing. A detector that
flags MaxDiff correctly and also refuses "Which factor is most important?"
does not protect anybody; it just moves the damage from the data file to the
review queue, where somebody learns to click past the check.
"""

from __future__ import annotations

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire.trade_off import (
    BEST_WORST,
    CONJOINT,
    MAXDIFF,
    apply,
    technique,
)


def _q(qid="Q1", text="", source=None, options=(), cols=(), rows=(), type=QuestionType.SINGLE):
    return Question(
        qid=qid,
        text=text,
        type=type,
        options=[Option(code=i + 1, label=o) for i, o in enumerate(options)],
        cols=[Option(code=i + 1, label=c) for i, c in enumerate(cols)] or None,
        rows=[Option(code=i + 1, label=r) for i, r in enumerate(rows)] or None,
        source_text=source if source is not None else text,
    )


# --- The technique is named -------------------------------------------------

def test_maxdiff_named_in_a_scripter_directive():
    q = _q(text="Of these features, which is most important?",
           source="Q20. [MAXDIFF EXERCISE — 12 SCREENS, 4 ITEMS PER SCREEN]\n"
                  "Of these features, which is most important?")
    assert technique(q) == MAXDIFF


def test_max_diff_spelled_apart():
    assert technique(_q(source="[MAX DIFF — 10 screens]")) == MAXDIFF
    assert technique(_q(source="[Max-Diff exercise]")) == MAXDIFF


def test_conjoint_named():
    assert technique(_q(source="[CONJOINT — 10 TASKS, 3 CONCEPTS + NONE]")) == CONJOINT
    assert technique(_q(source="This is a discrete choice exercise.")) == CONJOINT
    assert technique(_q(source="[PN: CHOICE-BASED EXERCISE]")) == CONJOINT


def test_best_worst_named():
    assert technique(_q(source="[BEST/WORST SCALING, 12 SETS]")) == BEST_WORST
    assert technique(_q(source="Best and worst exercise")) == BEST_WORST


# --- The machinery is named -------------------------------------------------

def test_design_machinery_without_the_technique_name():
    """A document that describes the design but never names it."""
    assert technique(_q(source="[PN: 4 items per screen, 12 screens, rotate]")) == MAXDIFF
    assert technique(
        _q(source="[PN: DESIGN FILE TO BE SUPPLIED BY ANALYTICS. 3 concepts per task.]")
    ) == CONJOINT
    assert technique(_q(source="[PN: SCRIPT IN SAWTOOTH]")) == MAXDIFF


def test_a_bare_acronym_is_not_enough():
    """Three capital letters are also a broadcaster and a clinic."""
    assert technique(_q(text="Have you seen this advertised on CBC?")) is None
    assert technique(_q(text="Which DCM service do you use?")) is None


def test_an_acronym_beside_a_design_word_counts():
    assert technique(_q(source="[PN: CBC exercise, design from analytics]")) == CONJOINT


# --- The shape, with nothing named ------------------------------------------

def test_two_column_most_least_grid_is_best_worst():
    q = _q(
        text="Which of these is most and least appealing?",
        type=QuestionType.GRID_SINGLE,
        rows=("Free delivery", "24-hour pharmacist", "Loyalty points", "App booking"),
        cols=("Most appealing", "Least appealing"),
    )
    assert technique(q) == BEST_WORST


def test_a_three_point_most_least_scale_is_an_ordinary_grid():
    """The guard that decides whether this detector is usable.

    "Most important / Somewhat important / Least important" is a rating scale
    a real questionnaire asks every day. Refusing to field it would make the
    check something reviewers learn to click past.
    """
    q = _q(
        text="How important is each of these to you?",
        type=QuestionType.GRID_SINGLE,
        rows=("Price", "Convenience", "Staff"),
        cols=("Most important", "Somewhat important", "Least important"),
    )
    assert technique(q) is None


def test_one_label_saying_both_is_not_a_trade_off():
    q = _q(
        type=QuestionType.GRID_SINGLE,
        rows=("Price",),
        cols=("Most and least important", "Neither"),
    )
    assert technique(q) is None


def test_an_ordinary_most_important_single_choice_is_left_alone():
    q = _q(
        text="Which of these is the most important factor when choosing a pharmacy?",
        options=("Price", "Location", "Opening hours", "Staff knowledge"),
    )
    assert technique(q) is None


def test_an_ordinary_ranking_question_is_left_alone():
    """"Rank these from best to worst" is a rank, and this platform fields it.

    The named-technique pattern deliberately requires "best-worst" / "best and
    worst" adjacent. "from best to worst" is how anybody describes a ranking,
    and refusing every rank question would be the detector's worst failure.
    """
    q = _q(
        text="Please rank these from best to worst.",
        type=QuestionType.RANK,
        options=("Brand A", "Brand B", "Brand C"),
    )
    assert technique(q) is None


# --- apply() ----------------------------------------------------------------

def test_apply_flags_and_records_the_technique():
    survey = SurveyDefinition(study_id="s1", questions=[
        _q(qid="Q1", text="How old are you?", options=("18-34", "35+")),
        _q(qid="Q20", source="[MAXDIFF EXERCISE — 12 SCREENS]"),
    ])
    assert apply(survey) == ["Q20"]
    assert schema.FLAG_TRADE_OFF_UNSUPPORTED in survey["Q20"].flags
    assert survey["Q20"].extraction_evidence["trade_off"] == MAXDIFF
    assert survey["Q1"].flags == []


def test_apply_is_idempotent():
    survey = SurveyDefinition(study_id="s1", questions=[_q(qid="Q20", source="[CONJOINT]")])
    apply(survey)
    apply(survey)
    assert survey["Q20"].flags.count(schema.FLAG_TRADE_OFF_UNSUPPORTED) == 1


# --- What three real questionnaires taught ----------------------------------

def test_a_note_about_a_later_exercise_does_not_condemn_this_question():
    """The false positive that made the detector unusable, pinned.

    In the Google Ads questionnaire an ordinary "what are the top barriers?"
    multi-select carries a spec note the author wrote himself about a section
    still to come. Reading the whole source text refused to field it.
    """
    q = _q(
        qid="GA_NON_BARRIER_META",
        text="Of these, what are the top barriers keeping your company from using Meta Ads?",
        source="META ADS LAPSED/NON-USER BARRIER\n"
               "Q Name: GA_NON_BARRIER_META\n"
               "Q type: Multi select\n"
               "Other details: (blank) motivators maxdiff exercise\n",
        options=("Cost", "Complexity", "No in-house skills"),
        type=QuestionType.MULTI,
    )
    assert technique(q) is None


def test_the_declared_type_cell_decides_it():
    """A spec table states the answer outright: "Q type: MaxDiff"."""
    q = _q(
        qid="Q30",
        text="Which is the most and which is the least likely to make you increase spend?",
        source="Q Name: Q30\nQ type: MaxDiff\nRandomize variable list: Choose an item\n",
    )
    assert technique(q) == MAXDIFF


def test_naming_another_question_is_not_being_that_question():
    """A pointer at the exercise is not the exercise.

    Underscores are word characters, so "MAXDIFF_LIKELY" in running text stays
    hidden from the pattern while the qid MAXDIFF_OE — whose underscores are
    turned to spaces because a question's own name speaks for it — does not.

    In the real questionnaire this variable IS flagged, on its heading
    ("MAXDIFF TOP BENEFIT APPEAL"), and rightly: it holds the MaxDiff result
    and cannot be computed without the exercise. What is tested here is only
    that the REFERENCE in its wording is not what decides that.
    """
    q = _q(
        qid="HID_TOPBENEFIT",
        text="Holds the most often selected Most Likely benefit from MAXDIFF_LIKELY "
             "to be inserted into MAXDIFF_OE question text",
        source="HIDDEN VARIABLE\nQ Name: HID_TOPBENEFIT\n",
    )
    assert technique(q) is None


def test_the_question_named_for_the_exercise_is_flagged():
    """MAXDIFF_OE is part of the module: its pipe has nothing behind it."""
    q = _q(qid="MAXDIFF_OE", text="Why would this make you shift digital ad spend?",
           source="MAXDIFF OE\nQ Name: MAXDIFF_OE\n")
    assert technique(q) == MAXDIFF


def test_display_text_is_never_a_trade_off_exercise():
    """The study-background blob names every technique the project uses."""
    q = _q(
        qid="UNKNOWN",
        text="Study Overview RESEARCH BACKGROUND: ... a MaxDiff exercise will measure ...",
        type=QuestionType.INFO,
    )
    assert technique(q) is None
