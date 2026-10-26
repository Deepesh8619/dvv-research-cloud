"""A question switched off for the link stays in the questionnaire but is never asked."""

from tests.integration.test_fielding_routes import (  # noqa: F401  (fixture)
    _create_link,
    _make_questionnaire,
    _make_study,
    _sample_survey,
    client,
)


def _qids(test_client, token):
    survey = test_client.get(f"/api/r/{token}").json()["surveyjs_json"]
    return {e["name"] for page in survey["pages"] for e in page.get("elements", [])}


def test_a_question_left_out_of_the_link_is_not_asked_but_stays_in_the_questionnaire(client):  # noqa: F811
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_sample_survey("study_f1"))
    base = "/api/studies/study_f1/questionnaire/questions"

    # Q2 decides who sees Q3: leaving it out would break Q3's routing.
    refused = test_client.put(f"{base}/Q2/in-link", json={"in_link": False})
    assert refused.status_code == 422 and refused.json()["detail"]["referenced_by"] == ["Q3"]

    assert test_client.put(f"{base}/Q3/in-link", json={"in_link": False}).status_code == 200
    assert any(q["qid"] == "Q3" for q in test_client.get(base).json()["questions"])
    variables = {v["name"] for v in test_client.get(
        "/api/studies/study_f1/questionnaire/datamap").json()["variables"]}
    assert "Q3" not in variables and "Q2" in variables

    # The status went back to review; approve again, then a new link leaves Q3 out.
    test_client.post("/api/studies/study_f1/questionnaire/approve-all")
    token = _create_link(test_client).json()["token"]
    assert "Q3" not in _qids(test_client, token) and "Q2" in _qids(test_client, token)
    dry = test_client.post("/api/studies/study_f1/fielding/dry-run", json={"n": 50}).json()
    assert all("Q3" not in p["qids"] for p in dry["paths"])

    assert test_client.put(f"{base}/Q3/in-link", json={"in_link": True}).status_code == 200
    test_client.post("/api/studies/study_f1/questionnaire/approve-all")
    assert "Q3" in _qids(test_client, _create_link(test_client).json()["token"])
