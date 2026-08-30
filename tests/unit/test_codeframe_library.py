"""Codeframe reuse: find a codeframe already built for this question.

The load-bearing tests are the ones proving it REFUSES to match. A missed
match costs one LLM call; a wrong match silently miscodes real respondent data
in a way nobody notices until the tables look strange.
"""

from __future__ import annotations

from src.engines.coding.library import (
    SUGGEST_FLOOR,
    content_words,
    find_matches,
    normalize_question,
    similarity,
)

# --------------------------------------------------------------------------
# Normalisation — scripter noise is not part of the question
# --------------------------------------------------------------------------


def test_question_ids_and_scripter_notes_are_stripped():
    """The same question with different scripting must normalise identically."""
    a = "Q12. Which brand do you use most often? (SINGLE)"
    b = "Which brand do you use most often?"
    assert normalize_question(a) == normalize_question(b)


def test_routing_instructions_are_stripped():
    a = "[ASK IF Q3=1] Why do you use that brand?"
    b = "Why do you use that brand?"
    assert normalize_question(a) == normalize_question(b)


def test_stopwords_do_not_distinguish_questions():
    assert content_words("Which of the brands do you use") == content_words(
        "Which brands you use"
    )


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------


def test_identical_question_scores_one_across_waves():
    """The tracker case: wave 2 asks exactly what wave 1 asked."""
    assert similarity(
        "Q4. Which brand do you use most often?",
        "Which brand do you use most often? (SINGLE)",
    ) == 1.0


def test_short_question_does_not_match_a_longer_one_just_by_being_a_subset():
    """Jaccard, not overlap — otherwise every short question matches everything."""
    score = similarity(
        "Which brand?",
        "Which brand do you buy for your children on weekday mornings?",
    )
    assert score < SUGGEST_FLOOR


def test_genuinely_different_questions_do_not_match():
    """A wrong codeframe corrupts data; this must stay well below the floor."""
    score = similarity(
        "Which toothpaste brand do you use most often?",
        "How satisfied are you with your mobile network provider?",
    )
    assert score < 0.3


def test_empty_text_scores_zero_rather_than_crashing():
    assert similarity("", "Which brand?") == 0.0
    assert similarity("Q1.", "") == 0.0


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def _entry(id_, text, reused=0, study="s1"):
    return {"id": id_, "question_text": text, "times_reused": reused, "study_id": study}


LIBRARY = [
    _entry("cf1", "Which brand do you use most often?", reused=4),
    _entry("cf2", "Why do you use that brand most often?", reused=1),
    _entry("cf3", "How satisfied are you with your mobile network?", reused=9),
]


def test_the_right_codeframe_is_offered_first():
    matches = find_matches("Q7. Which brand do you use most often? (SINGLE)", LIBRARY)
    assert matches[0].entry_id == "cf1"
    assert matches[0].exact is True
    assert "waves comparable" in matches[0].reason


def test_unrelated_questions_return_nothing():
    """Silence is the correct answer when nothing fits."""
    assert find_matches("What is your postcode?", LIBRARY) == []


def test_a_heavily_reused_codeframe_wins_a_tie():
    """A codeframe the team keeps reusing is the house standard."""
    lib = [
        _entry("rare", "Which brand do you use most often?", reused=0),
        _entry("standard", "Which brand do you use most often?", reused=12),
    ]
    assert find_matches("Which brand do you use most often?", lib)[0].entry_id == "standard"


def test_every_match_carries_a_reason_a_researcher_can_judge():
    """A score alone is not reviewable — the UI shows why it was offered."""
    for m in find_matches("Which brand do you use most often?", LIBRARY):
        assert m.reason and not m.reason[0].isdigit()


def test_blank_question_matches_nothing():
    assert find_matches("   ", LIBRARY) == []
    assert find_matches("Q1.", LIBRARY) == []
