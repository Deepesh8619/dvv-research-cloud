"""Does one analyst's heavy request make respondents wait?

Boots the real app on a spare port with a throwaway SQLite database, starts a
heavy analyst request (a large fielding dry run), and meanwhile times what a
respondent opening the survey link experiences. Run before and after a change:

    python scripts/bench_concurrency.py
"""

import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}/api"
ROOT = Path(__file__).resolve().parent.parent


def seed(env: dict) -> None:
    code = r'''
from datetime import datetime
from api.db import Base, engine, SessionLocal, UserDB, StudyDB, QuestionnaireDB
from api.auth import hash_password
from src.core.survey_definition import *
Base.metadata.create_all(bind=engine)
s = SessionLocal(); now = datetime.now().isoformat()
qs = [Question(qid="S1", text="Age", type=QuestionType.NUMERIC,
               terminate_if=Expression(op="lt", qid="S1", value=18))]
qs += [Question(qid=f"Q{i}", text=f"Q{i}", type=QuestionType.SINGLE,
                options=[Option(code=c, label=f"o{c}") for c in range(1, 6)]) for i in range(1, 40)]
sv = SurveyDefinition(study_id="st", title="bench", source="authored", status="approved", version=1,
                      blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])], questions=qs)
s.add(UserDB(id="u", email="bench@local.test", name="b", role="owner", is_active=True,
             created_at=now, password_hash=hash_password("bench-pw")))
s.add(StudyDB(id="st", client_name="b", status="classifying", created_at=now, updated_at=now,
              study_metadata={}, approval_chain={}))
s.add(QuestionnaireDB(id="q", study_id="st", status="approved", source_filename="b.docx", version=1,
                      definition_json=sv.to_json(), definition_path=None,
                      approved_qids=[q.qid for q in qs], created_at=now, updated_at=now))
s.commit()
'''
    subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)


def main() -> None:
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/bench.db", "STUDIES_DIR": tmp,
           "PYTHONPATH": str(ROOT), "ADMIN_PASSWORD": "bench-admin"}
    seed(env)
    workers = os.environ.get("BENCH_WORKERS", "1")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(PORT),
         "--workers", workers, "--log-level", "warning"],
        env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                if requests.get(f"{BASE}/health", timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        login = {"username": "bench@local.test", "password": "bench-pw"}
        jwt = requests.post(f"{BASE}/auth/login", data=login).json()["access_token"]
        h = {"Authorization": f"Bearer {jwt}"}
        token = requests.post(f"{BASE}/studies/st/fielding/links", headers=h,
                              json={"soft_launch_limit": None}).json()["token"]

        def probe(stop: threading.Event, out: list) -> None:
            while not stop.is_set():
                t = time.perf_counter()
                requests.get(f"{BASE}/r/{token}", timeout=120)
                out.append((time.perf_counter() - t) * 1000)
                time.sleep(0.05)

        baseline: list[float] = []
        stop = threading.Event()
        th = threading.Thread(target=probe, args=(stop, baseline))
        th.start()
        time.sleep(2)
        stop.set()
        th.join()

        during: list[float] = []
        stop = threading.Event()
        th = threading.Thread(target=probe, args=(stop, during))
        th.start()
        analysts = int(os.environ.get("BENCH_ANALYSTS", "4"))

        finished: list[float] = []

        def heavy_request() -> None:  # a 5,000-respondent dry run (the engine's cap)
            requests.post(f"{BASE}/studies/st/fielding/dry-run", headers=h,
                          json={"n": 5000}, timeout=600)
            finished.append(time.perf_counter() - t)

        t = time.perf_counter()
        crowd = [threading.Thread(target=heavy_request) for _ in range(analysts)]
        for c in crowd:
            c.start()
        for c in crowd:
            c.join()
        heavy = time.perf_counter() - t
        stop.set()
        th.join()

        def line(label: str, ms: list[float]) -> str:
            return (f"respondent opens link, {label}: median {statistics.median(ms):6.0f} ms"
                    f"   worst {max(ms):6.0f} ms   ({len(ms)} opens)")

        print(f"workers={workers}  {analysts} analysts, heavy requests at once: {heavy:.1f}s")
        print("each heavy request finished at: " + ", ".join(f"{x:.2f}s" for x in sorted(finished))
              + "   (staggered = serialized; together = parallel)")
        print(line("idle server ", baseline))
        print(line("during heavy", during))
    finally:
        server.terminate()
        server.wait(timeout=20)


if __name__ == "__main__":
    main()
