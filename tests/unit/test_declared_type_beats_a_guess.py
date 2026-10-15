"""A type the question STATES is not overturned by a shape found below it.

Q21d of the Vivo questionnaire says "[OPEN END]". Its chunk then runs on into
another section entirely: a header, a [PLACEHOLDER] table of campaign
creative, a "PN. CODE POSITION EXPOSURE" note, and a quota grid whose cells
read

    n=166-167 | n=166-167 | n=166-167

Three parallel columns look exactly like a scale, so the parser typed the
question GRID_SINGLE on the strength of it — before ever consulting the
question's own directive — and handed the verbatim ten answers made of the
neighbouring section's furniture.

A question that states it collects typed text or a number cannot be a grid,
whatever appears underneath it. The chunk boundary is still wrong, and that is
a separate defect; a stated type is simply not a thing to overturn on a guess.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.prose_directives import parse_prose_question

#: The real chunk, as the loader hands it over. Kept VERBATIM — the defect is
#: about what these exact lines look like to the parser, so rewrapping the
#: question's own line to satisfy a linter would defeat the fixture.
# ruff: noqa: E501
Q21D_CHUNK = """Q21d.[ASK Q21e IF USED VIVO AT Q13 (Q13.7=1-5) And where do you feel VIVOHealth Pharmacy falls short, if at all? [OPEN END]
Campaign Evaluation
1 | Radio/Streaming Audio
[PLACEHOLER] |
2 | Social
[PLACEHOLDER] |
3 | Display
[PLACEHOLDER] |
PN. CODE POSITION EXPOSURE AND QUESTIONS
 | 1st Position | 2nd Position | 3rd Position
RADIO/STREAMING AUDIO EXPOSUE + Q22-Q24 | n=166-167 | n=166-167 | n=166-167
SOCIAL EXPOSURE + Q25-Q27 | n=166-167 | n=166-167 | n=166-167
RADIO/STREAMING AUDIO"""


def test_a_declared_open_end_is_not_retyped_by_a_quota_grid():
    fields = parse_prose_question(Q21D_CHUNK, candidate_qid="Q21d")

    assert fields is not None
    assert fields.qtype is QuestionType.OPEN_TEXT
    assert fields.evidence["type"] == "[OPEN END]"


def test_the_neighbouring_sections_furniture_does_not_become_answers():
    """Ten options, and every one of them belonged to another section."""
    fields = parse_prose_question(Q21D_CHUNK, candidate_qid="Q21d")

    assert fields is not None
    labels = " ".join(o.label or "" for o in fields.options)
    assert fields.options == [], f"an open end took answers: {labels}"


def test_a_declared_numeric_is_not_retyped_either():
    """The same rule, for the other type that collects a typed answer."""
    chunk = (
        "Q9. How many people does your company employ? [NUMERIC]\n"
        "Headcount bands\n"
        " | Small | Medium | Large\n"
        "SITE A | n=50-60 | n=50-60 | n=50-60"
    )
    fields = parse_prose_question(chunk, candidate_qid="Q9")

    assert fields is not None
    assert fields.qtype is QuestionType.NUMERIC


# --- and what must STILL be read as a grid ---------------------------------


def test_a_real_scale_row_still_makes_a_grid():
    """The guard must not cost us the grids the scale-row rule exists for.

    Nothing here states a type, so the shape is the only evidence there is —
    which is exactly the case the scale-row branch was written for.
    """
    chunk = (
        "Q5. How satisfied are you with each of these pharmacies?\n"
        "Pharmacy | Very satisfied | Satisfied | Not satisfied\n"
        "CVS\n"
        "Walgreens"
    )
    fields = parse_prose_question(chunk, candidate_qid="Q5")

    assert fields is not None
    assert fields.qtype is QuestionType.GRID_SINGLE


def test_a_declared_grid_is_still_a_grid():
    """Only the types that collect a TYPED answer are protected."""
    chunk = (
        "Q6. Rate each brand. [SHOW STATEMENTS IN ROWS]\n"
        "1. Brand A\n"
        "2. Brand B"
    )
    fields = parse_prose_question(chunk, candidate_qid="Q6")

    assert fields is not None
    assert fields.qtype is QuestionType.GRID_SINGLE


def test_a_stated_cap_beats_one_answer_wording():
    chunk = "\n".join([
        "Which of the following best describes your perception of X? Select up to 2.",
        "Hopeful", "Unproven", "Unsure",
    ])
    fields = parse_prose_question(chunk, candidate_qid="Q10")
    assert fields.qtype is QuestionType.MULTI
    assert fields.select_limits.get("max_select") == 2
