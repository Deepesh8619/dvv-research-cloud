"""Intake classification: rule paths and router degradation (no live model needed)."""

from unittest.mock import patch

from src.ai.router import BackendUnavailable
from src.intake.classify import classify_file


def test_sav_classified_by_rule(tmp_path):
    f = tmp_path / "study.sav"
    f.write_bytes(b"\x00" * 10)
    result = classify_file(f)
    assert result.category == "data_file"
    assert result.method == "rule"
    assert result.confidence > 0.9


def test_parquet_classified_by_rule(tmp_path):
    f = tmp_path / "wave3.parquet"
    f.write_bytes(b"PAR1")
    assert classify_file(f).category == "data_file"


def test_graceful_when_backend_down(tmp_path):
    f = tmp_path / "mystery.csv"
    f.write_text("id,comment\n1,great product\n")
    with patch(
        "src.intake.classify.run_task", side_effect=BackendUnavailable("offline")
    ):
        result = classify_file(f)
    assert result.category == "unknown"
    assert result.confidence == 0.0


def test_llm_result_passed_through(tmp_path):
    f = tmp_path / "opens.csv"
    f.write_text("resp,verbatim\n1,love it\n2,too pricey\n")

    class Fake:
        content = {"category": "verbatims", "confidence": 0.92, "reason": "open-end text"}
        backend = "ollama"
        model = "llama3.1:8b"

    with patch("src.intake.classify.run_task", return_value=Fake()):
        result = classify_file(f)
    assert result.category == "verbatims"
    assert result.method == "ollama:llama3.1:8b"


# --- Word documents ---------------------------------------------------------
# A .docx is a ZIP, so the old code handed the model the archive header and a
# wall of NULs and asked it to categorise that. These cover both halves of the
# fix: real text is extracted, and the common case never reaches a model.


def _docx(path, paragraphs):
    from docx import Document

    doc = Document()
    for line in paragraphs:
        doc.add_paragraph(line)
    doc.save(path)
    return path


def test_questionnaire_docx_classified_without_a_model_call(tmp_path):
    f = _docx(
        tmp_path / "client_qre.docx",
        [
            "S1. What is your age?",
            "[PN: NUMERIC OE: 2-DIGIT MINIMUM]",
            "Under 18 [TERMINATE]",
            "18-34",
        ],
    )
    with patch("src.intake.classify.run_task", side_effect=AssertionError("no model")):
        result = classify_file(f)
    assert result.category == "questionnaire"
    assert result.method == "rule"
    assert "question labels" in result.reason


def test_one_stray_signal_is_not_enough_to_decide(tmp_path):
    """A datamap mentioning TERMINATE once must not be called a questionnaire."""
    f = _docx(
        tmp_path / "datamap.docx",
        ["Variable dictionary", "AGE — respondent age", "Note: TERMINATE flag stored here"],
    )
    with patch("src.intake.classify.run_task", side_effect=BackendUnavailable("offline")):
        result = classify_file(f)
    assert result.category != "questionnaire"


def test_model_gets_readable_text_not_zip_bytes(tmp_path):
    """The whole point of extraction: the preview must be words, not NULs."""
    f = _docx(tmp_path / "tab_plan.docx", ["Banner plan for Wave 3", "Table 1 by region"])
    seen = {}

    def fake_run_task(task, prompt, schema_arg, *args, **kwargs):
        seen["prompt"] = prompt
        raise BackendUnavailable("offline")

    with patch("src.intake.classify.run_task", side_effect=fake_run_task):
        classify_file(f)
    assert "Banner plan for Wave 3" in seen["prompt"]
    assert "PK\x03\x04" not in seen["prompt"]


def test_unreadable_docx_does_not_raise(tmp_path):
    f = tmp_path / "corrupt.docx"
    f.write_bytes(b"not a zip at all")
    with patch("src.intake.classify.run_task", side_effect=BackendUnavailable("offline")):
        result = classify_file(f)
    assert result.category == "unknown"  # degraded, not crashed
