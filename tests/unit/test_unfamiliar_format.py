"""A questionnaire in a house style the system has never seen.

The whole teaching loop, end to end: the decoding report names each unknown
convention with the lesson that would teach it; once taught (literal patterns,
no code), the same document reads completely.
"""

import docx

from src.engines.questionnaire.extractor import extract_survey_definition
from src.engines.questionnaire.learned import LearnedPattern
from src.intake.questionnaire_loader import load_docx, segment_into_questions

LINES = [
    "Coffee Habits Study",
    "Q1. [SINGLE PUNCH] Do you drink coffee at least once a week?", "1. Yes", "2. No [KILL]",
    "Q2. [MULTI PUNCH] Which of these brands have you bought in the past month? [SCRAMBLE]",
    "1. Brand A", "2. Brand B", "3. Brand C", "99. None of these [KILL]",
    "Q3. [MULTI PUNCH] What matters when you choose a coffee shop? [SCRAMBLE]",
    "1. Price", "2. Taste", "3. Location",
    "Q4. [OPEN] Why did you choose your favourite coffee shop?",
]


def _extract(tmp_path, patterns):
    d = docx.Document()
    for line in LINES:
        d.add_paragraph(line)
    path = tmp_path / "q.docx"
    d.save(path)
    blocks = load_docx(str(path))
    return extract_survey_definition(segment_into_questions(blocks), "s", blocks=blocks,
                                     patterns_for_profile=lambda slug: patterns)


def test_the_report_names_what_to_teach_and_teaching_completes_the_read(tmp_path):
    before = _extract(tmp_path, [])
    report = before.metadata["decoding"]
    assert report["verdict"] == "unfamiliar"
    suggested = {t["fragment"]: t["suggest"] for t in report["teach"]}
    assert suggested.get("[KILL]") == "option_tag"
    assert suggested.get("SCRAMBLE") == "randomize_signal"
    assert "[OPEN]" in suggested

    after = _extract(tmp_path, [
        LearnedPattern("option_tag", "[KILL]", "terminate"),
        LearnedPattern("randomize_signal", "[SCRAMBLE]"),
        LearnedPattern("type_signal", "[OPEN]", "open_text"),
    ])
    by = {q.qid: q for q in after.questions}
    assert after.metadata["decoding"]["verdict"] == "familiar"
    assert by["Q1"].terminate_if.values == [2]
    assert by["Q2"].randomize_options and by["Q2"].terminate_if.values == [99]
    assert by["Q4"].type.value == "open_text"
    assert after.metadata["decoding"]["teach"] == []
