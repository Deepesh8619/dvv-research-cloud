"""Intake: load an authored questionnaire (.docx/PDF) into candidate questions.

This is the first, *deterministic* stage of questionnaire ingestion. It reads a
market-research questionnaire document and normalizes it into a flat list of
`DocumentBlock`s (paragraphs, headings, tables) in document order, then splits
that list into `QuestionChunk`s using purely rule-based / regex detection of the
question-label conventions MR authors use (Q5, S3, A1a., SECTION B, TERMINATE…).

No LLM calls happen here — a later stage feeds these chunks to an LLM to parse
them into structured survey logic. Grid questions are almost always authored as
Word tables (rows = options, columns = statements or vice-versa); we keep those
as a distinct `TableBlock`-shaped block so the later stage can recognize a grid
instead of seeing a flattened wall of text.

Design mirrors the tabular/spss adapters (architecture §adapters: every format
maps into a canonical model; downstream never sees the source format). Errors are
raised with clear messages, never swallowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BlockType = Literal["paragraph", "table", "heading"]


@dataclass
class DocumentBlock:
    """One structural unit of a questionnaire, format-independent.

    For table blocks, `text` holds a serialized representation and `rows` holds
    the real grid (list of row-lists of cell strings) so a grid question is
    never flattened into plain paragraph text.
    """

    block_type: BlockType
    text: str
    paragraph_index: int
    page_number: int | None = None  # PDF only; None for .docx
    is_bold: bool = False
    is_heading: bool = False
    rows: list[list[str]] | None = None  # populated only for block_type == "table"


@dataclass
class QuestionChunk:
    """A candidate question: the blocks from one label line up to the next."""

    chunk_text: str
    candidate_qid: str | None
    source_blocks: list[DocumentBlock]
    provenance: str  # human-readable location, e.g. "page 3, para 12" or "para 47"


# --- Question-label detection (rule-based only) -----------------------------

# Q5, S3, A1a., Q12:, D2) — a screener/section letter + number, optional suffix
# letter, terminated by a separator so plain words like "Sales" don't match.
_QID_RE = re.compile(r"^\s*((?:Q|S|A|C|D)\d+[a-z]?)[.):]", re.IGNORECASE)
# SECTION B, SECTION 2 — a labelled section boundary with no question number.
_SECTION_RE = re.compile(r"^\s*SECTION\s+[A-Z0-9]", re.IGNORECASE)
# Strong instruction/boundary markers even without a question-number prefix.
_INSTRUCTION_RE = re.compile(r"^\s*(?:TERMINATE|SCREEN\s*OUT|SKIP\s*TO)\b", re.IGNORECASE)


def _detect_label(text: str) -> tuple[bool, str | None]:
    """Return (is_boundary, candidate_qid) for a block's leading text.

    A question label yields its qid (e.g. "Q5"); a SECTION/instruction boundary
    is a boundary with no qid. Anything else is not a boundary.
    """
    m = _QID_RE.match(text)
    if m:
        # Normalize the qid's leading letter to uppercase (q5 -> Q5).
        qid = m.group(1)
        return True, qid[0].upper() + qid[1:]
    if _SECTION_RE.match(text) or _INSTRUCTION_RE.match(text):
        return True, None
    return False, None


def _provenance(block: DocumentBlock) -> str:
    if block.page_number is not None:
        return f"page {block.page_number}, para {block.paragraph_index}"
    return f"para {block.paragraph_index}"


# --- .docx loading ----------------------------------------------------------


def load_docx(path: str | Path) -> list[DocumentBlock]:
    """Load a .docx into ordered DocumentBlocks (paragraphs, headings, tables).

    Paragraphs and tables are emitted in true document order. Bold is detected
    from a paragraph's runs; a paragraph is a heading if it uses a Word "Heading"
    style. Tables are kept as `rows` (list of row-lists) and never flattened.
    """
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - dependency guaranteed by requirements
        raise RuntimeError(
            "python-docx is required to load .docx questionnaires; "
            "install it (pip install python-docx>=1.0)"
        ) from exc

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"questionnaire .docx not found: {path}")

    document = Document(str(path))
    body = document.element.body
    blocks: list[DocumentBlock] = []
    para_index = 0

    # Map child XML elements back to python-docx objects to walk in order.
    para_map = {p._p: p for p in document.paragraphs}
    table_map = {t._tbl: t for t in document.tables}

    for child in body.iterchildren():
        if child in para_map:
            para: Paragraph = para_map[child]
            text = para.text.strip()
            if not text:
                continue
            is_bold = _para_is_bold(para)
            is_heading = (para.style.name or "").lower().startswith("heading")
            blocks.append(
                DocumentBlock(
                    block_type="heading" if is_heading else "paragraph",
                    text=text,
                    paragraph_index=para_index,
                    page_number=None,
                    is_bold=is_bold,
                    is_heading=is_heading,
                )
            )
            para_index += 1
        elif child in table_map:
            table: Table = table_map[child]
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            serialized = "\n".join(" | ".join(r) for r in rows)
            blocks.append(
                DocumentBlock(
                    block_type="table",
                    text=serialized,
                    paragraph_index=para_index,
                    page_number=None,
                    is_bold=False,
                    is_heading=False,
                    rows=rows,
                )
            )
            para_index += 1

    return blocks


def _para_is_bold(paragraph) -> bool:
    """A paragraph is 'bold' if it has runs and every non-empty run is bold."""
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False
    return all(bool(r.bold) for r in runs)


# --- PDF loading ------------------------------------------------------------

# Blocks whose left edges cluster into >1 group wider than this fraction of the
# page width are treated as a multi-column layout.
_COLUMN_GAP_FRACTION = 0.15


def load_pdf(path: str | Path) -> list[DocumentBlock]:
    """Load a PDF into ordered DocumentBlocks with reading-order awareness.

    Naive top-to-bottom extraction scrambles multi-column layouts, so per page
    we detect columns by clustering text blocks on their left x-coordinate and
    sort column-then-row. A block is flagged as a heading when its dominant font
    size is clearly larger than the page's body size (or it is bold).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency guaranteed by requirements
        raise RuntimeError(
            "PyMuPDF (fitz) is required to load PDF questionnaires; "
            "install it (pip install PyMuPDF>=1.24)"
        ) from exc

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"questionnaire PDF not found: {path}")

    blocks: list[DocumentBlock] = []
    para_index = 0
    with fitz.open(str(path)) as doc:
        for page_number, page in enumerate(doc, start=1):
            page_blocks = _extract_page_blocks(page)
            body_size = _body_font_size(page_blocks)
            for pb in page_blocks:
                is_heading = pb["is_bold"] or (
                    body_size is not None and pb["size"] > body_size * 1.15
                )
                blocks.append(
                    DocumentBlock(
                        block_type="heading" if is_heading else "paragraph",
                        text=pb["text"],
                        paragraph_index=para_index,
                        page_number=page_number,
                        is_bold=pb["is_bold"],
                        is_heading=is_heading,
                    )
                )
                para_index += 1
    return blocks


def _extract_page_blocks(page) -> list[dict]:
    """Return this page's text blocks sorted in column-then-row reading order.

    Each dict carries: text, x0, y0, size (dominant font size), is_bold.
    """
    raw = page.get_text("dict")
    page_width = page.rect.width or 1.0
    parsed: list[dict] = []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:  # skip image blocks
            continue
        lines = block.get("lines", [])
        text_parts: list[str] = []
        sizes: list[float] = []
        bold_flags: list[bool] = []
        for line in lines:
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text.strip():
                    continue
                text_parts.append(span_text)
                sizes.append(float(span.get("size", 0.0)))
                flags = int(span.get("flags", 0))
                # PyMuPDF font flag bit 4 (value 16) marks bold.
                bold_flags.append(bool(flags & 16))
        text = " ".join(text_parts).strip()
        if not text:
            continue
        x0, y0 = block["bbox"][0], block["bbox"][1]
        parsed.append(
            {
                "text": text,
                "x0": x0,
                "y0": y0,
                "size": max(sizes) if sizes else 0.0,
                "is_bold": any(bold_flags),
            }
        )

    return _sort_reading_order(parsed, page_width)


def _sort_reading_order(parsed: list[dict], page_width: float) -> list[dict]:
    """Sort blocks column-then-row when a multi-column layout is detected."""
    if len(parsed) < 2:
        return parsed

    columns = _cluster_columns([b["x0"] for b in parsed], page_width)
    if len(columns) < 2:
        # Single column: plain top-to-bottom (then left-to-right) order.
        return sorted(parsed, key=lambda b: (round(b["y0"]), b["x0"]))

    def column_index(x0: float) -> int:
        return min(range(len(columns)), key=lambda i: abs(x0 - columns[i]))

    # Column first (left-to-right), then row (top-to-bottom) within a column.
    return sorted(parsed, key=lambda b: (column_index(b["x0"]), round(b["y0"])))


def _cluster_columns(x_positions: list[float], page_width: float) -> list[float]:
    """Cluster left x-coordinates into column anchors (their mean x).

    Two anchors closer than `_COLUMN_GAP_FRACTION` of the page width are merged,
    so incidental indentation doesn't read as a second column.
    """
    gap = page_width * _COLUMN_GAP_FRACTION
    anchors: list[list[float]] = []
    for x in sorted(x_positions):
        placed = False
        for group in anchors:
            if abs(x - (sum(group) / len(group))) <= gap:
                group.append(x)
                placed = True
                break
        if not placed:
            anchors.append([x])
    return [sum(group) / len(group) for group in anchors]


def _body_font_size(page_blocks: list[dict]) -> float | None:
    """Most common (rounded) font size on the page — its body-text size."""
    sizes = [round(b["size"]) for b in page_blocks if b["size"] > 0]
    if not sizes:
        return None
    return float(max(set(sizes), key=sizes.count))


# --- Segmentation -----------------------------------------------------------


def segment_into_questions(blocks: list[DocumentBlock]) -> list[QuestionChunk]:
    """Split ordered blocks into candidate questions on detected label lines.

    A chunk runs from one detected question-label / section / instruction line up
    to (not including) the next detected label, accumulating every block in
    between (including tables, which usually form that question's answer grid).
    Leading content before the first label is emitted as its own chunk with
    `candidate_qid=None` so nothing is silently dropped.
    """
    chunks: list[QuestionChunk] = []
    current: list[DocumentBlock] = []
    current_qid: str | None = None
    seen_label = False

    def flush() -> None:
        if not current:
            return
        chunks.append(
            QuestionChunk(
                chunk_text="\n".join(b.text for b in current).strip(),
                candidate_qid=current_qid,
                source_blocks=list(current),
                provenance=_provenance(current[0]),
            )
        )

    for block in blocks:
        is_boundary, qid = _detect_label(block.text)
        if is_boundary:
            flush()
            current = [block]
            current_qid = qid
            seen_label = True
        else:
            if not seen_label and not current:
                # Preamble before any label: start a headless (qid=None) chunk.
                current = [block]
                current_qid = None
            else:
                current.append(block)

    flush()
    return chunks
