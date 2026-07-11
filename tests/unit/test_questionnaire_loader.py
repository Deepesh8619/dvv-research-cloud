"""Questionnaire loader: docx/PDF ingestion and rule-based segmentation.

Fixtures are generated programmatically (no real client files): a synthetic
.docx is authored with python-docx, and PDF-specific reading-order logic is
tested against a real synthetic PDF built with PyMuPDF plus direct unit tests of
the column-sorting helper. The shared `segment_into_questions` is also tested on
a hand-built block list so the segmentation rules are covered independently of
any file I/O.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.intake.questionnaire_loader import (
    DocumentBlock,
    _cluster_columns,
    _sort_reading_order,
    load_docx,
    load_pdf,
    segment_into_questions,
)

# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Client Satisfaction Survey")  # preamble, no label
    doc.add_paragraph("Q1. What is your age?")

    section = doc.add_paragraph()
    run = section.add_run("SECTION B — SCREENER")
    run.bold = True

    doc.add_paragraph("S3. TERMINATE IF NOT AWARE")

    doc.add_paragraph("Q5. How much do you agree with each statement?")
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text = ""
    table.cell(0, 1).text = "Agree"
    table.cell(1, 0).text = "Product is good value"
    table.cell(1, 1).text = "5"
    table.cell(2, 0).text = "Product is reliable"
    table.cell(2, 1).text = "4"

    out = tmp_path / "questionnaire.docx"
    doc.save(str(out))
    return out


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Q1. What is your age?", fontsize=11)
    page.insert_text((72, 130), "Q2. What is your gender?", fontsize=11)
    out = tmp_path / "questionnaire.pdf"
    doc.save(str(out))
    doc.close()
    return out


# --- docx loading + segmentation --------------------------------------------


def test_docx_loads_paragraphs_and_table_without_flattening(sample_docx: Path) -> None:
    blocks = load_docx(sample_docx)

    table_blocks = [b for b in blocks if b.block_type == "table"]
    assert len(table_blocks) == 1
    tbl = table_blocks[0]
    # The grid must be preserved as rows, not flattened into a paragraph.
    assert tbl.rows is not None
    assert tbl.rows[1] == ["Product is good value", "5"]
    assert tbl.rows[2] == ["Product is reliable", "4"]
    # page_number is None for docx.
    assert all(b.page_number is None for b in blocks)


def test_docx_bold_section_detected(sample_docx: Path) -> None:
    blocks = load_docx(sample_docx)
    section = next(b for b in blocks if b.text.startswith("SECTION B"))
    assert section.is_bold is True


def test_docx_segmentation_produces_expected_chunks(sample_docx: Path) -> None:
    blocks = load_docx(sample_docx)
    chunks = segment_into_questions(blocks)

    qids = [c.candidate_qid for c in chunks]
    # Preamble (None), Q1, SECTION (None), S3, Q5.
    assert qids == [None, "Q1", None, "S3", "Q5"]

    # The preamble chunk carries the title and is not dropped.
    assert "Client Satisfaction Survey" in chunks[0].chunk_text

    # The table belongs to the Q5 chunk and is retained as a table block.
    q5 = chunks[-1]
    assert q5.candidate_qid == "Q5"
    assert any(b.block_type == "table" for b in q5.source_blocks)


def test_docx_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_docx("/nonexistent/questionnaire.docx")


# --- PDF loading ------------------------------------------------------------


def test_pdf_loads_and_segments(sample_pdf: Path) -> None:
    blocks = load_pdf(sample_pdf)
    assert all(b.page_number == 1 for b in blocks)

    chunks = segment_into_questions(blocks)
    assert [c.candidate_qid for c in chunks] == ["Q1", "Q2"]
    assert chunks[0].provenance.startswith("page 1")


def test_pdf_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_pdf("/nonexistent/questionnaire.pdf")


# --- reading-order helpers (unit, no I/O) -----------------------------------


def test_cluster_columns_detects_two_columns() -> None:
    # Left edges at ~50 and ~350 on a 600-wide page: two clusters.
    cols = _cluster_columns([50, 52, 350, 351], page_width=600)
    assert len(cols) == 2


def test_cluster_columns_merges_incidental_indent() -> None:
    # 50 and 60 are within the 15% gap (90px) on a 600 page: one column.
    cols = _cluster_columns([50, 60, 55], page_width=600)
    assert len(cols) == 1


def test_sort_reading_order_two_columns_column_then_row() -> None:
    # Left column (x0=50) rows at y=100,200; right column (x0=350) at y=100,200.
    parsed = [
        {"text": "R-top", "x0": 350, "y0": 100, "size": 11, "is_bold": False},
        {"text": "L-top", "x0": 50, "y0": 100, "size": 11, "is_bold": False},
        {"text": "R-bot", "x0": 350, "y0": 200, "size": 11, "is_bold": False},
        {"text": "L-bot", "x0": 50, "y0": 200, "size": 11, "is_bold": False},
    ]
    ordered = [b["text"] for b in _sort_reading_order(parsed, page_width=600)]
    # Full left column first, then full right column.
    assert ordered == ["L-top", "L-bot", "R-top", "R-bot"]


def test_sort_reading_order_single_column_top_to_bottom() -> None:
    parsed = [
        {"text": "b", "x0": 50, "y0": 200, "size": 11, "is_bold": False},
        {"text": "a", "x0": 50, "y0": 100, "size": 11, "is_bold": False},
    ]
    ordered = [b["text"] for b in _sort_reading_order(parsed, page_width=600)]
    assert ordered == ["a", "b"]


# --- segmentation on hand-built blocks --------------------------------------


def _para(text: str, idx: int) -> DocumentBlock:
    return DocumentBlock(block_type="paragraph", text=text, paragraph_index=idx)


def test_segment_instruction_marker_is_a_boundary() -> None:
    blocks = [
        _para("Q1. Intro question", 0),
        _para("SKIP TO Q5 IF UNDER 18", 1),
        _para("Q5. Main question", 2),
    ]
    chunks = segment_into_questions(blocks)
    assert [c.candidate_qid for c in chunks] == ["Q1", None, "Q5"]
    # The SKIP TO instruction is its own chunk, not swallowed into Q1.
    assert "SKIP TO Q5" in chunks[1].chunk_text


def test_segment_qid_normalizes_case_and_suffix() -> None:
    blocks = [_para("a1a. subquestion", 0), _para("q12: another", 1)]
    chunks = segment_into_questions(blocks)
    assert [c.candidate_qid for c in chunks] == ["A1a", "Q12"]


def test_segment_plain_word_is_not_a_label() -> None:
    blocks = [_para("Some intro text about the study", 0), _para("Q1. Real question", 1)]
    chunks = segment_into_questions(blocks)
    assert chunks[0].candidate_qid is None
    assert chunks[1].candidate_qid == "Q1"


def test_segment_arbitrary_letter_prefixes_split() -> None:
    """Pilot regression: a real screener used P1-P3 (parent path) and T1-T5
    (teen path); the old Q|S|A|C|D whitelist glued them onto the preceding
    question. Any short letter prefix + number must start a new chunk."""
    blocks = [
        _para("S6. Which stores have you shopped at?", 0),
        _para("P1. Are you the parent or guardian of any teenagers?", 1),
        _para("T1. How old are you?", 2),
        _para("PN2. Two-letter prefix question", 3),
    ]
    chunks = segment_into_questions(blocks)
    assert [c.candidate_qid for c in chunks] == ["S6", "P1", "T1", "PN2"]


def test_segment_table_stays_with_preceding_question() -> None:
    tbl = DocumentBlock(
        block_type="table",
        text="Row A | 5",
        paragraph_index=1,
        rows=[["Row A", "5"]],
    )
    blocks = [_para("Q7. Grid question", 0), tbl, _para("Q8. Next", 2)]
    chunks = segment_into_questions(blocks)
    q7 = chunks[0]
    assert q7.candidate_qid == "Q7"
    assert any(b.block_type == "table" for b in q7.source_blocks)
