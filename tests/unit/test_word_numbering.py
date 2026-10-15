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


def test_answer_codes_are_the_numbers_word_prints(tmp_path):
    """Word numbers 'Don't Know' 99; counting lines made it 6 in the data."""
    from src.core.survey_definition import Expression, Option, Question, QuestionType
    from src.engines.questionnaire.extractor import _codes_from_list_numbers
    from src.intake.questionnaire_loader import DocumentBlock, QuestionChunk

    blocks = [DocumentBlock("paragraph", "S6. Covered by insurance?", 0),
              DocumentBlock("paragraph", "Yes", 1, list_label="1"),
              DocumentBlock("paragraph", "No [TERMINATE]", 2, list_label="5"),
              DocumentBlock("paragraph", "Don’t Know [TERMINATE]", 3, list_label="99")]
    chunk = QuestionChunk(chunk_text="\n".join(b.text for b in blocks), candidate_qid="S6",
                          source_blocks=blocks, provenance="")
    q = Question(qid="S6", text="Covered?", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Yes"), Option(code=2, label="No"),
                          Option(code=3, label="Don’t Know")],
                 terminate_if=Expression(op="in", qid="S6", values=[2, 3]))
    _codes_from_list_numbers(q, chunk)
    assert [o.code for o in q.options] == [1, 5, 99]
    assert q.terminate_if.values == [5, 99]
    # One answer without a printed number: nothing is renumbered.
    q2 = Question(qid="S6", text="Covered?", type=QuestionType.SINGLE,
                  options=[Option(code=1, label="Yes"), Option(code=2, label="Maybe")])
    _codes_from_list_numbers(q2, chunk)
    assert [o.code for o in q2.options] == [1, 2]
