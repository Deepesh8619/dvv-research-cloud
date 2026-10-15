"""Word auto-numbering: the number a reader sees, which is not in the text."""

import docx

from src.intake.questionnaire_loader import load_docx, segment_into_questions


def _doc(tmp_path, lines):
    d = docx.Document()
    for style, text in lines:
        d.add_paragraph(text, style=style)
    path = tmp_path / "q.docx"
    d.save(path)
    return str(path)


def test_list_numbers_are_read_as_the_reader_sees_them(tmp_path):
    path = _doc(tmp_path, [
        ("List Number", "[OPTIONAL GET EMAIL ADDRESS]"),
        ("List Number", "[BASE: ALL] How many patients do you see?"),
        ("Normal", "A plain paragraph"),
        ("List Bullet", "a bullet"),
        ("List Number", "[BASE: ALL] Are you familiar with TRD?"),
    ])
    labels = [(b.list_label, b.text[:12]) for b in load_docx(path)]
    assert labels == [("1.", "[OPTIONAL GE"), ("2.", "[BASE: ALL] "), (None, "A plain para"),
                      (None, "a bullet"), ("3.", "[BASE: ALL] ")]


def test_unnumbered_text_takes_the_list_number_as_its_name(tmp_path):
    """The first list item is a note, so the first question is Q2 — as printed."""
    path = _doc(tmp_path, [
        ("List Number", "[OPTIONAL GET EMAIL ADDRESS]"),
        ("List Number", "[BASE: ALL] How many patients do you see?"),
        ("Normal", "[BASE: ANSWERED NO ABOVE] TRD means the following."),
        ("List Number", "Please describe why you would prescribe it"),
        ("Normal", "A. Thinking about your patients, what share do you refer?"),
    ])
    chunks = segment_into_questions(load_docx(path))
    assert [c.candidate_qid for c in chunks if c.candidate_qid] == ["Q2", "Q2_2", "Q3", "Q3a"]
