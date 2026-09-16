"""Live copilot smoke test against the real open models (Groq/Cerebras keys from .env).

Boots the app on a spare port with a throwaway SQLite database and a SYNTHETIC
project — never client data — then asks the copilot real questions and prints
what it looked up, what it answered and what it proposed.

    python scripts/smoke_copilot.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}/api"
ROOT = Path(__file__).resolve().parent.parent

SEED = r'''
from datetime import datetime
from api.db import (Base, engine, SessionLocal, UserDB, StudyDB, QuestionnaireDB,
                    create_fielding_link, create_respondent_session, upsert_respondent_answer,
                    set_respondent_session_status)
from api.auth import hash_password
from src.core.survey_definition import *
Base.metadata.create_all(bind=engine)
s = SessionLocal(); now = datetime.now().isoformat()
qs = [
  Question(qid="S1", text="What is your age?", type=QuestionType.NUMERIC,
           terminate_if=Expression(op="lt", qid="S1", value=18)),
  Question(qid="Q1", text="Do you drink coffee?", type=QuestionType.SINGLE,
           options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
           terminate_if=Expression(op="eq", qid="Q1", value=2)),
  Question(qid="Q2", text="Which coffee brands do you know?", type=QuestionType.MULTI,
           options=[Option(code=1, label="Alpha"), Option(code=2, label="Beta")]),
  Question(qid="Q3", text="Why do you prefer Alpha?", type=QuestionType.OPEN_TEXT, required=False,
           show_if=Expression(op="selected", qid="Q2", value=1)),
]
sv = SurveyDefinition(study_id="coffee", title="Coffee U&A", source="authored", status="approved",
                      version=1, blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                      questions=qs)
s.add(UserDB(id="u", email="smoke@local.test", name="Smoke Tester", role="owner", is_active=True,
             created_at=now, password_hash=hash_password("smoke-pw")))
s.add(StudyDB(id="coffee", client_name="Coffee U&A (synthetic)", status="classifying",
              created_at=now, updated_at=now, study_metadata={}, approval_chain={}))
s.add(QuestionnaireDB(id="q", study_id="coffee", status="approved", source_filename="coffee.docx",
                      version=1, definition_json=sv.to_json(), definition_path=None,
                      approved_qids=[q.qid for q in qs], created_at=now, updated_at=now))
s.commit()
link = create_fielding_link(s, "coffee", "q", 1, sv.to_json(), "smoke@local.test")
link.soft_launch_limit = 50; s.commit()
people = [(34, 1, [1, 2]), (16, None, None), (41, 2, None),
          (29, 1, [2]), (52, 2, None), (38, 1, [1])]
for age, coffee, brands in people:
    ses = create_respondent_session(s, link.id)
    upsert_respondent_answer(s, ses.id, "S1", age)
    if coffee: upsert_respondent_answer(s, ses.id, "Q1", coffee)
    if brands: upsert_respondent_answer(s, ses.id, "Q2", brands)
    screened = age < 18 or coffee == 2
    set_respondent_session_status(s, ses.id, 20 if screened else 10)
print(link.id)
'''

QUESTIONS = [
    "What projects do we have, and where does each one stand?",
    "Why are people being screened out on the coffee study, and what's the incidence?",
    "Who gets asked Q3?",
    "Everything looks fine. Release the coffee link to full sample.",
]


def main() -> None:
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/smoke.db", "STUDIES_DIR": tmp,
           "PYTHONPATH": str(ROOT), "ADMIN_PASSWORD": "smoke-admin"}
    subprocess.run([sys.executable, "-c", SEED], env=env, cwd=ROOT, check=True,
                   capture_output=True, text=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(PORT),
         "--log-level", "warning"],
        env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                if requests.get(f"{BASE}/health", timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        login = {"username": "smoke@local.test", "password": "smoke-pw"}
        jwt = requests.post(f"{BASE}/auth/login", data=login).json()["access_token"]
        h = {"Authorization": f"Bearer {jwt}"}
        history: list[dict] = []
        for q in QUESTIONS:
            t = time.perf_counter()
            r = requests.post(f"{BASE}/copilot", headers=h, timeout=180,
                              json={"message": q, "history": history, "study_id": "coffee"})
            took = time.perf_counter() - t
            print(f"\n>>> {q}")
            if not r.ok:
                print(f"    HTTP {r.status_code}: {r.text[:300]}")
                continue
            body = r.json()
            print(f"    [{body['answered_by']}, {took:.1f}s] tools: {body['tools_used']}")
            print("    " + body["reply"].replace("\n", "\n    "))
            for card in body["proposals"]:
                print(f"    CARD: {card['title']} — {card['detail']}")
                print(f"          on confirm: {card['method']} {card['path']} {card['body']}")
            history += [{"role": "user", "content": q},
                        {"role": "assistant", "content": body["reply"][:3900]}]
    finally:
        server.terminate()
        server.wait(timeout=20)


if __name__ == "__main__":
    main()
