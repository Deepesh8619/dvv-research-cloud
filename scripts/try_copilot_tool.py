"""Run one Copilot tool on a real data file, on this computer, with no AI model.

When the Copilot gets something wrong, this shows whether the TOOL is wrong
(fix the code) or the MODEL chose badly (fix the prompt or the tool's refusal).
Everything stays in a throwaway folder; nothing touches the server.

    python scripts/try_copilot_tool.py DATA.xlsx make_tables '{"columns": ["S3"]}'
    python scripts/try_copilot_tool.py DATA.xlsx make_tables '{"rows": ["Q13"]}'
    python scripts/try_copilot_tool.py DATA.xlsx ask_data '{"question": "Q4 by S1"}'

The first run processes the file (about a minute); later runs on the same file
reuse it. study_id is filled in for you.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    data, tool = Path(sys.argv[1]).expanduser(), sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    work = Path.home() / ".dvv-try" / data.stem.replace(" ", "_")
    work.mkdir(parents=True, exist_ok=True)
    os.environ.update(DATABASE_URL=f"sqlite:///{work}/try.db", STUDIES_DIR=str(work / "studies"),
                      BEDROCK_API_KEY="", GROQ_API_KEY="", CEREBRAS_API_KEY="", HF_TOKEN="")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from fastapi import Depends
    from fastapi.testclient import TestClient

    import src.ai.copilot as copilot
    from api.auth import get_current_user
    from api.db import Base, SessionLocal, StudyDB, UserDB, engine, get_db
    from api.main import app

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if db.get(UserDB, "try") is None:
        db.add(UserDB(id="try", email="try@local", name="Try", role="owner", is_active=True))
        db.commit()
    app.dependency_overrides[get_current_user] = (
        lambda db=Depends(get_db): db.get(UserDB, "try"))  # noqa: B008
    client = TestClient(app)

    study = db.query(StudyDB).first()
    if study is None:
        print(f"Processing {data.name} (first run only)…")
        with data.open("rb") as f:
            sid = client.post("/api/studies/upload", files={"file": (data.name, f)},
                              data={"client_name": data.stem}).json()["study_id"]
        while client.get(f"/api/studies/{sid}").json().get("status") in (
                "uploaded", "processing", "classifying", "queued"):
            time.sleep(2)
        study = db.query(StudyDB).first()
    args["study_id"] = study.id

    # The model is replaced by a script: call the tool once, then stop.
    turns = iter([{"tool_calls": [{"id": "t1", "type": "function", "function": {
        "name": tool, "arguments": json.dumps(args)}}]}])

    def scripted(*_a, **_k):
        msg = next(turns, {"content": "(done)"})
        return {"choices": [{"message": {"role": "assistant", "content": None, **msg}}]}

    copilot._compatible_chat = scripted
    seen: list[dict] = []
    real = copilot._run_agent_loop if hasattr(copilot, "_run_agent_loop") else None
    if real:
        def spy(*a, **k):
            reply, results = real(*a, **k)
            seen.extend(results)
            return reply, results
        copilot._run_agent_loop = spy
    res = client.post("/api/copilot", json={"message": f"{tool} {args}", "study_id": study.id})
    body = res.json()
    print(f"\nHTTP {res.status_code}")
    for r in seen:
        print(json.dumps({k: v for k, v in r.items() if k not in ("tables",)}, indent=1,
                         default=str)[:3000])
    print(f"\nTables shown in the reply: {len(body.get('tables') or [])}")
    for t in (body.get("tables") or [])[:2]:
        print(" ", t["header"])
    for f in body.get("charts") or []:
        print(f"File: {f['title']} -> {work / 'studies'}/**/charts/{f['filename']}")
    if body.get("tool_errors"):
        print("Refused:", *body["tool_errors"], sep="\n  ")


if __name__ == "__main__":  # the heavy-work pool starts new processes that re-import this file
    main()
