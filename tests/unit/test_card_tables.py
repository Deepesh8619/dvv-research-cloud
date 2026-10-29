"""Question cards: one Word table per question, read by layout, never guessed.

Built here from scratch with python-docx — the shape of a real tracker, none of
its words.
"""

from docx import Document

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.extractor import extract_survey_definition
from src.intake.questionnaire_loader import load_docx, segment_into_questions


def _fill(cell, lines: list[str]) -> None:
    """One paragraph per line, as Word writes a list typed into a cell."""
    cell.text = lines[0] if lines else ""
    for line in lines[1:]:
        cell.add_paragraph(line)


def _card(doc, head: list[str], middle: list[list[list[str]]], notes: list[str]):
    """A card: merged head row, the answer rows, a merged notes row."""
    width = max(len(row) for row in middle)
    table = doc.add_table(rows=len(middle) + 2, cols=width)
    top = table.rows[0].cells[0].merge(table.rows[0].cells[-1])
    _fill(top, head)
    for r, row in enumerate(middle, 1):
        cells = table.rows[r].cells
        if len(row) < width:
            cells[len(row) - 1].merge(cells[-1])
        for c, lines in enumerate(row):
            _fill(cells[c], lines)
    bottom = table.rows[-1].cells[0].merge(table.rows[-1].cells[-1])
    _fill(bottom, notes)


def _document(tmp_path):
    doc = Document()
    doc.add_paragraph("Telecom tracker")
    heading = doc.add_table(rows=2, cols=1)
    heading.rows[0].cells[0].text = "SCREENER QUESTIONS: QS1-QS3"
    heading.rows[1].cells[0].text = "Ask all."
    _card(doc, ["GENDER", "QGENDER.", "Are you?", "Please select one."],
          [[["1", "2", "98 99"], ["Male", "Female", "Other", "Prefer not to say"]]],
          ["Allow one response."])
    _card(doc, ["TENURE", "S5d.", "How long have you had service?", "Please select one."],
          [[["1", "2", "98"], ["Less than a year", "A year or more", "I don't remember"]]],
          ["Ask if punch 1 selected at S2.", "Allow one response, do not randomize."])
    _card(doc, ["SWITCH", "IP4.", "How likely are you to switch?",
                "Please make one selection below."],
          [[["1 – Very unlikely", "[1]"], ["2 – Unsure", "[2]"], ["3 – Very likely", "[3]"]],
           [["[99] I plan to drop the service"]]],
          ["Accept one response."])
    _card(doc, ["RATINGS", "CS1.", "How satisfied are you with each?", "One response for each."],
          [[[], [], ["1 – Dissatisfied", "[1]"], ["2 – Neutral", "[2]"], ["3 – Satisfied", "[3]"]],
           [["1"], ["Speed"], [], [], []],
           [["2"], ["Courtesy"], [], [], []]],
          ["Allow one response per attribute."])
    path = tmp_path / "cards.docx"
    doc.save(path)
    return path


def test_every_card_is_a_labelled_question_read_without_a_model(tmp_path):
    blocks = load_docx(_document(tmp_path))
    chunks = segment_into_questions(blocks)
    assert [c.candidate_qid for c in chunks if c.candidate_qid] == ["QGENDER", "S5d", "IP4", "CS1"]

    survey = extract_survey_definition(chunks, "T", blocks=blocks)
    by = {q.qid: q for q in survey.questions}

    tenure = by["S5d"]
    assert tenure.text == "How long have you had service?"  # the label is not wording
    assert [(o.code, o.label) for o in tenure.options] == [
        (1, "Less than a year"), (2, "A year or more"), (98, "I don't remember")]
    assert tenure.type is QuestionType.SINGLE

    switch = by["IP4"]
    assert switch.type is QuestionType.SINGLE  # "Please make one selection below."
    assert [o.code for o in switch.options] == [1, 2, 3, 99]
    assert switch.options[0].label == "Very unlikely"

    grid = by["CS1"]
    assert grid.type is QuestionType.GRID_SINGLE
    assert [(r.code, r.label) for r in grid.rows] == [(1, "Speed"), (2, "Courtesy")]
    assert [(c.code, c.label) for c in grid.cols] == [
        (1, "Dissatisfied"), (2, "Neutral"), (3, "Satisfied")]

    cards = ("QGENDER", "S5d", "IP4", "CS1")
    assert all(by[q].extraction_method == "prose_directives" for q in cards)


def test_a_code_box_that_does_not_line_up_is_said_not_guessed(tmp_path):
    """"98 99" in one box splits cleanly; codes that cannot be paired are
    reported for a person instead of being matched to answers by position."""
    blocks = load_docx(_document(tmp_path))
    chunks = segment_into_questions(blocks)
    text = next(c.chunk_text for c in chunks if c.candidate_qid == "QGENDER")
    assert "98\tOther" in text and "99\tPrefer not to say" in text


def test_a_document_with_one_labelled_table_is_left_alone(tmp_path):
    doc = Document()
    doc.add_paragraph("Q1. Are you?")
    doc.add_paragraph("Male")
    _card(doc, ["X", "Q2.", "Rate it"], [[["1"], ["Good"]]], ["Note"])
    path = tmp_path / "one.docx"
    doc.save(path)
    assert any(b.block_type == "table" for b in load_docx(path))
