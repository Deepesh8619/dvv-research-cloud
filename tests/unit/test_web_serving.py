"""One origin serves the API and the web app — and deep links must survive.

A respondent link a panel hands out is a deep link (/r/<token>). If a refresh
or a first visit to it 404s, the survey is unreachable, so this is the
contract that matters, not the home page.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import mount_web


def _client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root>APP</div>")
    (tmp_path / "assets" / "app.js").write_text("console.log('js')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("do not serve")
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    mount_web(app, tmp_path)
    return TestClient(app)


def test_deep_links_get_the_app_shell(tmp_path):
    client = _client(tmp_path)
    for path in ("/", "/r/AbC123token", "/projects/s1/fielding"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "APP" in resp.text, path


def test_built_files_are_served_as_themselves(tmp_path):
    client = _client(tmp_path)
    assert client.get("/assets/app.js").text == "console.log('js')"
    assert client.get("/favicon.svg").text == "<svg/>"


def test_api_routes_win_and_unknown_api_paths_stay_json_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/health").json() == {"status": "ok"}
    missing = client.get("/api/no-such-thing")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}


def test_no_path_escapes_the_build_directory(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/..%2Fsecret.txt")
    assert "do not serve" not in resp.text
