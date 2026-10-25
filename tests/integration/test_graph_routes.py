"""G1: graph routes, and approve-all writing the graph."""

from unittest.mock import patch

from tests.integration.test_fielding_routes import (  # noqa: F401
    _make_questionnaire,
    _make_study,
    _sample_survey,
    client,
)


def test_approve_writes_graph_and_routes_answer(client):  # noqa: F811
    c, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, status="reviewing")
    r = c.post("/api/studies/study_f1/questionnaire/approve-all")
    assert r.status_code == 200, r.text
    up = c.get("/api/studies/study_f1/graph/lineage", params={"node": "question:Q3"})
    assert up.status_code == 200
    assert [n["ref"] for n in up.json()] == ["Q2"]
    down = c.get("/api/studies/study_f1/graph/impact", params={"node": "question:Q2"})
    assert [n["ref"] for n in down.json()] == ["Q3"]


def test_unknown_node_and_study_404(client):  # noqa: F811
    c, sf, _ = client
    _make_study(sf)
    assert c.get("/api/studies/study_f1/graph/impact",
                 params={"node": "question:Nope"}).status_code == 404
    assert c.get("/api/studies/study_f1/graph/impact",
                 params={"node": "badformat"}).status_code == 404
    assert c.get("/api/studies/nope/graph/impact",
                 params={"node": "question:Q1"}).status_code == 404


def test_broken_graph_write_still_approves(client):  # noqa: F811
    c, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, status="reviewing")
    with patch("src.engines.questionnaire.graph_sync.sync_questionnaire",
               side_effect=RuntimeError("boom")):
        r = c.post("/api/studies/study_f1/questionnaire/approve-all")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
