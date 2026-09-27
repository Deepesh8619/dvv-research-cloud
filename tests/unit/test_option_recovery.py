"""Answers that were in the document and not in the survey.

Both bugs here were found on real client questionnaires, and both are the same
severity: an option that never reaches the respondent cannot be chosen, and the
resulting distribution looks like a real finding.
"""

from src.engines.questionnaire.prose_directives import parse_prose_question


def _labels(text, qid="Q1"):
    fields = parse_prose_question(text, qid)
    return [o.label for o in fields.options], fields.text


def test_the_first_option_is_not_absorbed_into_the_question_wording():
    """A stem that wraps does not look like the line under it; the first item of
    a list looks exactly like the second.

    Measured on the Vivo questionnaire: Q17d's five-point scale arrived with
    four points because "Very likely" had been read as the tail of the stem,
    Q4a's "Yes" was gone, and Q10 lost both its opt-out codes.
    """
    labels, text = _labels(
        "Q17d. How likely would you be to switch pharmacies?\n"
        "Very likely\nSomewhat likely\nNeither likely nor unlikely\n"
        "Not very likely\nNot at all likely"
    )
    assert labels == [
        "Very likely", "Somewhat likely", "Neither likely nor unlikely",
        "Not very likely", "Not at all likely",
    ]
    assert text == "How likely would you be to switch pharmacies?"


def test_a_genuinely_wrapped_stem_is_still_joined():
    """The rule this guards exists for a real reason and must keep working."""
    labels, text = _labels(
        "Q1. Thinking about the last time you bought skincare,\n"
        "which brand did you choose?\n1. Alpha\n2. Beta"
    )
    assert text == (
        "Thinking about the last time you bought skincare, "
        "which brand did you choose?"
    )
    assert labels == ["Alpha", "Beta"]


def test_a_numbered_list_that_lost_its_separator_is_still_numbered():
    """"1Acne or breakouts" — found on two Bubble questions.

    The code pattern needs a separator and a space, so nothing stripped the
    digit and the respondent was being shown it.
    """
    fields = parse_prose_question(
        "Q35. Which skin concerns do you have?\n"
        "1Acne or breakouts\n2Redness or rosacea\n3Eczema\n4Dryness",
        "Q35",
    )
    assert [(o.code, o.label) for o in fields.options] == [
        (1, "Acne or breakouts"), (2, "Redness or rosacea"),
        (3, "Eczema"), (4, "Dryness"),
    ]


def test_a_label_that_merely_starts_with_a_number_is_left_alone():
    """"1st choice" split into code 1 and the label "st choice", and lost the
    first option entirely. A real label carries on in lower case; a mangled
    code leaves a capital behind."""
    assert _labels("Q1. Rank these\n1st choice\n2nd choice\n3rd choice")[0] == [
        "1st choice", "2nd choice", "3rd choice",
    ]
    assert _labels("Q1. How often?\n1x per week\n2x per week\n3x per week")[0] == [
        "1x per week", "2x per week", "3x per week",
    ]
    assert _labels("Q1. How many?\n10 or more\n20 or more\n30 or more")[0] == [
        "10 or more", "20 or more", "30 or more",
    ]
