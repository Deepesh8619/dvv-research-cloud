"""LLM safety net over the deterministic regex segmenter.

`questionnaire_loader.segment_into_questions` is regex-only by design (no LLM,
fast, free) — but real client documents keep exposing question formats the regex
can't anticipate (P1/T1 prefixes, then GBK's table-per-question layout, ...).
This module adds ONE cheap-if-nothing-missed LLM pass that runs over the WHOLE
document AFTER regex segmentation and only tries to recover what regex missed:

  1. every block is numbered by its position in the DocumentBlock list;
  2. the qids/ranges the regex already found are handed to the model so it skips
     them and reports only MISSED questions, each anchored to a block INDEX (not
     a fragile text snippet — an index is exact on merged-cell tables);
  3. each valid finding re-splices the chunk list, pulling the missed question
     (and any note blocks that belong with it) into its own chunk.

Failure-tolerant like the rest of the pipeline: if the backend is unavailable
the original chunks are returned unchanged and a document-level flag is recorded
in the caller's metadata dict — extraction never blocks on this being available.

This is the SECOND stage's helper (LLM-touching), deliberately NOT in
questionnaire_loader.py whose no-LLM invariant other code depends on.
"""

from __future__ import annotations

import logging

from src.ai.router import BackendUnavailable, ModelOutputError, run_task
from src.engines.questionnaire import schema
from src.intake.questionnaire_loader import DocumentBlock, QuestionChunk

logger = logging.getLogger(__name__)

# Above this many blocks, split the reconciliation call into overlapping windows
# rather than sending one giant prompt. 120 is a deliberate proxy for "a single
# call might blow a context/cost budget": a real 73-question programming QRE
# loads as ~110 blocks, so the common real document stays a single call (keeping
# the pass cheap) while genuinely long docs get windowed.
WINDOW_THRESHOLD = 120
# 100-block windows stepping by 90 give a 10-block overlap, so a question sitting
# right on a window edge (its notes in one window, its start in the next) is
# still seen whole by at least one window.
WINDOW_SIZE = 100
WINDOW_OVERLAP = 10
# Per-block text sent to the model is truncated to keep the prompt bounded — the
# model anchors on indices, not on reading every option of every grid.
_BLOCK_SNIPPET_CHARS = 300


def _provenance(block: DocumentBlock) -> str:
    """Human-readable location for a rebuilt chunk (mirrors the loader's form)."""
    if block.page_number is not None:
        return f"page {block.page_number}, para {block.paragraph_index}"
    return f"para {block.paragraph_index}"


def _windows(n: int) -> list[tuple[int, int]]:
    """Global (start, end) block slices to reconcile — one, unless n is large."""
    if n <= WINDOW_THRESHOLD:
        return [(0, n)]
    windows: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + WINDOW_SIZE, n)
        windows.append((start, end))
        if end == n:
            break
        start += WINDOW_SIZE - WINDOW_OVERLAP
    return windows


def _number_blocks(blocks: list[DocumentBlock], ws: int, we: int) -> str:
    """Serialize blocks[ws:we] with zero-padded GLOBAL index prefixes."""
    lines = []
    for i in range(ws, we):
        b = blocks[i]
        snippet = " ".join(b.text.split())[:_BLOCK_SNIPPET_CHARS]
        lines.append(f"[{i:03d}] ({b.block_type}) {snippet}")
    return "\n".join(lines)


def _known_ranges_text(ranges: list[tuple[int, int, str | None]], ws: int, we: int) -> str:
    """List the already-found qids + ranges that overlap this window."""
    rows = [
        f"  {qid or '(no label)'}: {s}-{e}"
        for (s, e, qid) in ranges
        if s < we and e >= ws
    ]
    return "\n".join(rows) if rows else "  (none)"


def _chunk_ranges(
    blocks: list[DocumentBlock], chunks: list[QuestionChunk]
) -> list[tuple[int, int, str | None]] | None:
    """(start, end, qid) block range per chunk, by object identity (exact).

    Identity (`id(block)`) is used rather than text/equality matching:
    segment_into_questions puts the SAME block objects into each chunk's
    source_blocks, so their positions in the original list are unambiguous even
    when two blocks share identical text. Returns None if any chunk can't be
    placed (e.g. empty source_blocks) — the caller then skips reconciliation
    rather than risk dropping a chunk it can't anchor.
    """
    pos = {id(b): i for i, b in enumerate(blocks)}
    ranges: list[tuple[int, int, str | None]] = []
    for ch in chunks:
        idxs = [pos[id(b)] for b in ch.source_blocks if id(b) in pos]
        if not idxs:
            return None
        ranges.append((min(idxs), max(idxs), ch.candidate_qid))
    return ranges


def _accept_findings(
    findings: list,
    n_blocks: int,
    ranges: list[tuple[int, int, str | None]],
) -> list[tuple[int, str | None]]:
    """Validate raw LLM findings into accepted (boundary_index, qid) splits.

    Dropped silently (bad LLM output must never crash): out-of-range index, a
    qid colliding with an already-known or already-accepted qid, or a start that
    coincides with an existing chunk's start (that's re-reporting a question the
    regex already found correctly). An interior start is exactly the split we
    want. note_block_indices that sit between the containing chunk's start and
    the missed start are pulled in by moving the boundary earlier.
    """
    known_qids = {q for _, _, q in ranges if q}
    chunk_starts = {s for s, _, _ in ranges}
    accepted: list[tuple[int, str | None]] = []
    used_qids = set(known_qids)
    used_starts: set[int] = set()
    for f in findings:
        if not isinstance(f, dict):
            continue
        start = f.get("start_block_index")
        if not isinstance(start, int) or not (0 <= start < n_blocks):
            continue
        if start in chunk_starts or start in used_starts:
            continue
        qid = f.get("qid")
        qid = str(qid).strip() if isinstance(qid, str) and qid.strip() else None
        if qid and qid in used_qids:
            continue
        containing = next(((s, e) for (s, e, _) in ranges if s <= start <= e), None)
        if containing is None:
            continue
        notes = f.get("note_block_indices") or []
        valid_notes = [
            nn for nn in notes if isinstance(nn, int) and containing[0] < nn < start
        ]
        boundary = min([start, *valid_notes])
        accepted.append((boundary, qid))
        used_starts.add(start)
        if qid:
            used_qids.add(qid)
    return accepted


def _resplice(
    blocks: list[DocumentBlock],
    ranges: list[tuple[int, int, str | None]],
    accepted: list[tuple[int, str | None]],
) -> list[QuestionChunk]:
    """Rebuild the chunk list by partitioning blocks at every boundary.

    Boundaries = existing chunk starts + accepted split points. Because regex
    chunks are contiguous and gapless, partitioning the block list at the sorted
    boundary set reproduces unchanged chunks byte-for-byte and inserts the
    recovered ones in document order.
    """
    boundaries: dict[int, str | None] = {s: q for (s, _, q) in ranges}
    for bidx, qid in accepted:
        boundaries[bidx] = qid  # never collides with an existing chunk start
    ordered = sorted(boundaries.items())
    new_chunks: list[QuestionChunk] = []
    for k, (bi, qid) in enumerate(ordered):
        end = ordered[k + 1][0] if k + 1 < len(ordered) else len(blocks)
        span = blocks[bi:end]
        if not span:
            continue
        new_chunks.append(
            QuestionChunk(
                chunk_text="\n".join(b.text for b in span).strip(),
                candidate_qid=qid,
                source_blocks=list(span),
                provenance=_provenance(span[0]),
            )
        )
    return new_chunks


def reconcile_segmentation(
    blocks: list[DocumentBlock],
    chunks: list[QuestionChunk],
    metadata: dict | None = None,
) -> list[QuestionChunk]:
    """Recover regex-missed questions via one whole-document LLM pass.

    Returns a re-spliced chunk list (recovered questions inserted in document
    order) or the ORIGINAL chunks unchanged when nothing is missed or the backend
    is unavailable. On unavailability a `FLAG_RECONCILIATION_UNAVAILABLE` entry is
    written into `metadata` (a document-level event, not a per-question flag) so
    the degradation is inspectable — extraction is never blocked on this pass.
    """
    if not blocks or not chunks:
        return chunks
    ranges = _chunk_ranges(blocks, chunks)
    if ranges is None:
        return chunks  # can't anchor a chunk safely — leave regex result alone

    findings: list = []
    for ws, we in _windows(len(blocks)):
        prompt = schema.build_reconcile_prompt(
            _number_blocks(blocks, ws, we),
            _known_ranges_text(ranges, ws, we),
        )
        try:
            response = run_task(
                "questionnaire.reconcile_segmentation", prompt, schema.RECONCILE_SCHEMA
            )
        except (BackendUnavailable, ModelOutputError, KeyError) as exc:
            logger.debug("segmentation reconciliation unavailable: %s", exc)
            if metadata is not None:
                metadata[schema.FLAG_RECONCILIATION_UNAVAILABLE] = str(exc)
            return chunks  # conservative: apply no partial reconciliation
        findings.extend(response.content.get("missed_questions") or [])

    accepted = _accept_findings(findings, len(blocks), ranges)
    if not accepted:
        return chunks
    return _resplice(blocks, ranges, accepted)


def _demo() -> None:
    """Self-check: a question glued into the preamble is re-split out. No LLM."""
    blocks = [
        DocumentBlock("paragraph", "Client Satisfaction Survey", 0),
        DocumentBlock("paragraph", "How old are you in years?", 1),  # missed question
        DocumentBlock("paragraph", "Q2. Are you aware of Brand X?", 2),
    ]
    chunks = [
        QuestionChunk("Client Satisfaction Survey\nHow old are you in years?", None,
                      blocks[:2], "para 0"),
        QuestionChunk("Q2. Are you aware of Brand X?", "Q2", blocks[2:], "para 2"),
    ]
    ranges = _chunk_ranges(blocks, chunks)
    assert ranges == [(0, 1, None), (2, 2, "Q2")]
    accepted = _accept_findings(
        [{"qid": "RESP_AGE", "start_block_index": 1, "note_block_indices": []}],
        len(blocks),
        ranges,
    )
    out = _resplice(blocks, ranges, accepted)
    qids = [c.candidate_qid for c in out]
    assert qids == [None, "RESP_AGE", "Q2"], qids
    assert out[1].chunk_text == "How old are you in years?", out[1].chunk_text
    # Collision + out-of-range findings are dropped, leaving chunks untouched.
    assert _accept_findings(
        [{"qid": "Q2", "start_block_index": 1}, {"start_block_index": 99}], len(blocks), ranges
    ) == []
    print("reconciler demo OK")


if __name__ == "__main__":  # pragma: no cover - manual self-check
    _demo()
