"""When a link will finish, from its real sessions — via the API and the copilot."""

import json
from datetime import datetime, timedelta

from api.db import FieldingLinkDB, RespondentSessionDB
from tests.integration.test_copilot import _script, _tool_results
from tests.integration.test_fielding_routes import (  # noqa: F401 - `client` is a fixture
    _make_questionnaire,
    _make_study,
    client,
)


def _field(sf, link_id: str, per_day: list[tuple[int, int]], target: int | None = None):
    """Sessions over the past days: per_day[i] = (completes, screen-outs) about i+0.5 days ago."""
    db = sf()
    try:
        link = db.query(FieldingLinkDB).filter_by(id=link_id).one()
        link.target_completes = target
        now = datetime.now()
        n = 0
        for days_ago, (done, out) in enumerate(per_day, start=1):
            for status in [10] * done + [20] * out:
                start = now - timedelta(days=days_ago - 0.5)
                n += 1
                db.add(RespondentSessionDB(
                    id=f"rs_{link_id}_{n}", fielding_link_id=link_id, status_code=status,
                    entry_params={}, started_at=start.isoformat(),
                    # Same instant: 10 minutes later crossed midnight when the suite ran
                    # near noon, and the daily table grew a row.
                    completed_at=start.isoformat()))
        db.commit()
    finally:
        db.close()


def test_the_api_projects_the_finish_from_the_recent_pace(client):  # noqa: F811
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post("/api/studies/study_f1/fielding/links").json()
    # 4 completes a day for the last 3 days, 1 a day the 4 days before; 1 screen-out each.
    _field(sf, link["id"], [(4, 4)] * 3 + [(1, 1)] * 4, target=100)
    p = test_client.get(f"/api/studies/study_f1/fielding/links/{link['id']}/projection").json()
    assert p["completes"] == 16 and p["incidence_pct"] == 50.0
    assert p["rate_per_day"] == 4.0 and p["rate_basis"] == "12 completes in the last 3 days"
    (target,) = [g for g in p["goals"] if g["name"] == "target"]
    assert target["remaining"] == 84 and target["eta_days"] == 21.0
    assert target["starts_needed"] == 168  # 2 starts per complete


def test_the_copilot_reports_the_projection_and_its_daily_table(client, monkeypatch):  # noqa: F811
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post("/api/studies/study_f1/fielding/links").json()
    _field(sf, link["id"], [(2, 1)] * 5, target=40)
    seen = _script(monkeypatch,
                   ("tool", "field_projection", {"study_id": "study_f1", "link_id": link["id"]}),
                   ("text", "At about 2 a day, the target is reached in 15 days."))
    resp = test_client.post("/api/copilot", json={"message": "when do we finish?"}).json()
    (result,) = _tool_results(seen)
    assert result["completes"] == 10
    assert "Do not recompute" in result["note"]
    (daily,) = resp["tables"]
    assert daily["header"] == ["Day", "Starts", "Completes"] and len(daily["rows"]) == 5
    assert json.dumps(resp).count("field_projection") >= 1
