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
