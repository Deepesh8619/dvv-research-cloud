"""Hybrid retrieval: question codes, words and meaning fused; findings and lessons searchable."""

import json

from api.db import CorrectionDB, ProfileRuleDB, StudyDB
from tests.integration.test_copilot import _script, _tool_results
from tests.integration.test_fielding_routes import (  # noqa: F401 - `client` is a fixture
    _make_questionnaire,
    _make_study,
    _sample_survey,
    client,
)


def _project_with_history(sf):
    """The sample questionnaire, read from a known format, with a QC finding,
    a correction and a taught rule."""
    _make_study(sf)
    survey = _sample_survey("study_f1")
    survey.metadata["profile"] = {"slug": "acme_fmt"}
    _make_questionnaire(sf, survey=survey)
    db = sf()
    try:
        db.query(StudyDB).filter_by(id="study_f1").one().crosscheck_json = json.dumps({
            "findings": [{"qid": "Q1", "kind": "screen_out_leak", "severity": "high",
                          "detail": "Respondents under 18 completed the survey", "n_rows": 3}]})
        db.add(CorrectionDB(id="corr_1", study_id="study_f1", question_id="Q2", author_id="u1",
                            instruction="The grid rows were read as separate questions",
                            status="resolved", created_at="2026-10-27"))
        db.add(ProfileRuleDB(id="rule_1", profile_slug="acme_fmt",
                             rule_text="[SA] marks a single-choice question", pattern="[SA]",
                             pattern_kind="type_signal", pattern_value="single", active=True,
                             created_at="2026-10-27"))
        db.commit()
    finally:
        db.close()


def _search(test_client, monkeypatch, query: str) -> dict:
    seen = _script(monkeypatch,
                   ("tool", "search_project", {"study_id": "study_f1", "query": query}),
                   ("text", "Found it."))
    test_client.post("/api/copilot", json={"message": query})
    (result,) = _tool_results(seen)
    return result


def test_a_question_code_comes_first_and_every_hit_cites(client, monkeypatch):  # noqa: F811
    test_client, sf, _ = client
    _project_with_history(sf)
    result = _search(test_client, monkeypatch, "what does Q3 ask")
    first = result["matches"][0]
    assert first["cite"] == "question:Q3" and first["matched_by"][0] == "code"
    assert first["depends_on"] == ["Q2"]  # GraphRAG neighbours travel with the hit
    assert all(m["cite"].count(":") >= 1 for m in result["matches"])


def test_qc_findings_and_lessons_are_searchable(client, monkeypatch):  # noqa: F811
    test_client, sf, _ = client
    _project_with_history(sf)
    finding = _search(test_client, monkeypatch, "anyone under 18 completing")["matches"][0]
    assert finding["cite"] == "finding:finding-1"
    assert "words" in finding["matched_by"]

    lessons = {m["cite"] for m in _search(test_client, monkeypatch,
                                          "grid rows read as separate questions")["matches"]}
    assert "lesson:correction-corr_1" in lessons
    taught = {m["cite"] for m in _search(test_client, monkeypatch,
                                         "single-choice marker SA")["matches"]}
    assert "lesson:rule-rule_1" in taught


def test_a_changed_finding_reindexes(client, monkeypatch):  # noqa: F811
    test_client, sf, _ = client
    _project_with_history(sf)
    _search(test_client, monkeypatch, "under 18")
    db = sf()
    try:
        db.query(StudyDB).filter_by(id="study_f1").one().crosscheck_json = json.dumps({
            "findings": [{"qid": "Q2", "kind": "base", "severity": "low",
                          "detail": "Coffee drinkers base is short by two"}]})
        db.commit()
    finally:
        db.close()
    texts = [m["text"] for m in _search(test_client, monkeypatch, "base short")["matches"]]
    assert any("short by two" in t for t in texts)
    assert not any("under 18" in t for t in texts)
