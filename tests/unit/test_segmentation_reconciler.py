"""Segmentation reconciliation: the LLM safety net over the regex segmenter.

`run_task` is patched (as imported into the reconciler / extractor modules) so
these are fast and deterministic — no real API calls. Every finding the mocked
model returns is anchored to a block index; the tests assert the re-splice,
the drop-on-bad-finding paths, failure tolerance, and windowing.
"""

from __future__ import annotations

from unittest.mock import patch

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import QuestionType
from src.engines.questionnaire import schema
from src.engines.questionnaire.reconciler import reconcile_segmentation
from src.intake.questionnaire_loader import DocumentBlock, QuestionChunk

_RECONCILE = "src.engines.questionnaire.reconciler.run_task"
_EXTRACT = "src.engines.questionnaire.extractor.run_task"


def _block(text: str, i: int, btype: str = "paragraph") -> DocumentBlock:
    return DocumentBlock(block_type=btype, text=text, paragraph_index=i)


def _response(missed: list[dict]) -> AIResponse:
    return AIResponse(
        content={"missed_questions": missed}, backend="claude", model="test"
    )


def _chunk(text: str, qid: str | None, blocks: list[DocumentBlock]) -> QuestionChunk:
    return QuestionChunk(
        chunk_text=text,
        candidate_qid=qid,
        source_blocks=blocks,
        provenance=f"para {blocks[0].paragraph_index}",
    )


# --- recovery of a missed question -----------------------------------------


def test_prose_question_glued_into_preamble_is_re_split_out():
    blocks = [
        _block("Client Satisfaction Survey", 0),
        _block("How old are you in years?", 1),  # a real question, no label/table
        _block("Q2. Are you aware of Brand X?", 2),
    ]
    # Regex under-segmented: the unlabeled question was absorbed into the preamble.
    chunks = [
        _chunk("Client Satisfaction Survey\nHow old are you in years?", None, blocks[:2]),
        _chunk("Q2. Are you aware of Brand X?", "Q2", blocks[2:]),
    ]

    with patch(
        _RECONCILE,
        return_value=_response(
            [{"qid": "RESP_AGE", "start_block_index": 1, "note_block_indices": []}]
        ),
    ):
        out = reconcile_segmentation(blocks, chunks)

    assert [c.candidate_qid for c in out] == [None, "RESP_AGE", "Q2"]
    recovered = out[1]
    assert recovered.chunk_text == "How old are you in years?"
    assert recovered.source_blocks == [blocks[1]]
    # The preamble chunk keeps only its own block now.
    assert out[0].source_blocks == [blocks[0]]


def test_preceding_note_block_is_pulled_into_the_recovered_chunk():
    blocks = [
        _block("Client Satisfaction Survey", 0),
        _block("SCRIPTER NOTE: randomize the list below", 1),  # belongs WITH the question
        _block("Which of these have you bought?", 2),  # missed question
        _block("Q3. Age?", 3),
    ]
    chunks = [
        _chunk(
            "Client Satisfaction Survey\nSCRIPTER NOTE: randomize the list below\n"
            "Which of these have you bought?",
            None,
            blocks[:3],
        ),
        _chunk("Q3. Age?", "Q3", blocks[3:]),
    ]

    with patch(
        _RECONCILE,
        return_value=_response(
            [{"qid": "BUYLIST", "start_block_index": 2, "note_block_indices": [1]}]
        ),
    ):
        out = reconcile_segmentation(blocks, chunks)

    assert [c.candidate_qid for c in out] == [None, "BUYLIST", "Q3"]
    # The note rides WITH the recovered question, not the preamble.
    assert out[1].source_blocks == [blocks[1], blocks[2]]
    assert "SCRIPTER NOTE" in out[1].chunk_text
    assert out[0].source_blocks == [blocks[0]]


# --- bad findings are dropped, never crash ---------------------------------


def test_finding_colliding_with_known_qid_is_dropped():
    blocks = [_block("Preamble", 0), _block("prose q", 1), _block("Q2. Real?", 2)]
    chunks = [
        _chunk("Preamble\nprose q", None, blocks[:2]),
        _chunk("Q2. Real?", "Q2", blocks[2:]),
    ]

    with patch(
        _RECONCILE,
        return_value=_response([{"qid": "Q2", "start_block_index": 1}]),  # collides
    ):
        out = reconcile_segmentation(blocks, chunks)

    # Nothing accepted -> original chunk list is returned unchanged.
    assert out is chunks


def test_out_of_range_index_is_dropped():
    blocks = [_block("Preamble", 0), _block("prose q", 1), _block("Q2. Real?", 2)]
    chunks = [
        _chunk("Preamble\nprose q", None, blocks[:2]),
        _chunk("Q2. Real?", "Q2", blocks[2:]),
    ]

    with patch(
        _RECONCILE,
        return_value=_response([{"qid": "GHOST", "start_block_index": 99}]),
    ):
        out = reconcile_segmentation(blocks, chunks)

    assert out is chunks


# --- failure tolerance ------------------------------------------------------


def test_backend_unavailable_returns_original_and_flags_metadata():
    blocks = [_block("Preamble", 0), _block("prose q", 1)]
    chunks = [_chunk("Preamble\nprose q", None, blocks)]
    meta: dict = {}

    with patch(_RECONCILE, side_effect=BackendUnavailable("no key", config_error=True)):
        out = reconcile_segmentation(blocks, chunks, meta)

    assert out is chunks  # never raises, never blocks extraction
    assert schema.FLAG_RECONCILIATION_UNAVAILABLE in meta


# --- windowing for long documents ------------------------------------------


def test_long_document_is_windowed_and_findings_merged():
    # 130 blocks -> two windows: (0,100) and (90,130). One chunk spans them all.
    blocks = [_block(f"line {i}", i) for i in range(130)]
    chunks = [_chunk("\n".join(b.text for b in blocks), None, blocks)]

    responses = [
        _response([{"qid": "A", "start_block_index": 50, "note_block_indices": []}]),
        # Second window re-reports A (overlap region) and adds B.
        _response(
            [
                {"qid": "A", "start_block_index": 50, "note_block_indices": []},
                {"qid": "B", "start_block_index": 110, "note_block_indices": []},
            ]
        ),
    ]

    with patch(_RECONCILE, side_effect=responses) as mock_run:
        out = reconcile_segmentation(blocks, chunks)

    assert mock_run.call_count == 2  # windowing kicked in
    qids = [c.candidate_qid for c in out]
    assert qids.count("A") == 1  # de-duplicated across the overlap
    assert qids.count("B") == 1
    assert qids == [None, "A", "B"]


# --- full pipeline: reconciliation feeds the extract loop -------------------


def test_extract_survey_definition_recovers_a_missed_question_end_to_end():
    from src.engines.questionnaire.extractor import extract_survey_definition

    blocks = [
        _block("Client Satisfaction Survey", 0),
        _block("How old are you in years?", 1),  # regex-missed question
        _block("Q2. Are you aware of Brand X?", 2),
    ]
    chunks = [
        _chunk("Client Satisfaction Survey\nHow old are you in years?", None, blocks[:2]),
        _chunk("Q2. Are you aware of Brand X?", "Q2", blocks[2:]),
    ]

    def fake_extract(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return AIResponse(
                content={"faithful": True, "discrepancies": []},
                backend="claude",
                model="test",
            )
        if "How old" in prompt:
            qid, qtype = "RESP_AGE", "numeric"
        elif "Brand X" in prompt:
            qid, qtype = "Q2", "single"
        else:  # the preamble/title chunk
            qid, qtype = "TITLE", "info"
        return AIResponse(
            content={
                "qid": qid,
                "text": "recovered",
                "type": qtype,
                "options": [],
                "routing": {
                    "verbatim_text": "",
                    "ambiguous": False,
                    "show_if": None,
                    "terminate_if": None,
                },
                "confidence": {"text": 0.9, "type": 0.9, "options": 0.9, "routing": 0.9},
            },
            backend="claude",
            model="test",
        )

    with (
        patch(
            _RECONCILE,
            return_value=_response(
                [{"qid": "RESP_AGE", "start_block_index": 1, "note_block_indices": []}]
            ),
        ),
        patch(_EXTRACT, side_effect=fake_extract),
    ):
        survey = extract_survey_definition(chunks, study_id="study-recon", blocks=blocks)

    qids = [q.qid for q in survey.questions]
    assert qids == ["TITLE", "RESP_AGE", "Q2"]  # the recovered question is in order
    recovered = survey["RESP_AGE"]
    assert recovered.type == QuestionType.NUMERIC
