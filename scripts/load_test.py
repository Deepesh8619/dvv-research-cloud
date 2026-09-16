"""Load test: can one server carry N concurrent users of the real product?

Simulates the actual mix — most concurrent users are RESPONDENTS moving through
a survey (a page of answers every few seconds), a few are ANALYSTS watching the
field monitor and running the odd heavy report — and reports latency and
errors per kind of request.

    # Against a server you already run (e.g. the client's cloud server):
    python scripts/load_test.py --base-url https://research.acme.com \\
        --email owner@acme.com --password '...' --study <study_id>

    # Boot the app here against a database of your choice (Postgres for real numbers):
    DATABASE_URL=postgresql://... python scripts/load_test.py --local

SQLite serialises writes and will report lock errors that Postgres never has —
only a Postgres run says anything about production capacity.
"""

import argparse
import os
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ModuleNotFoundError:  # only the load GENERATOR needs it, not --seed-only/--cleanup
    requests = None

ROOT = Path(__file__).resolve().parent.parent
N_QUESTIONS = 10

_lat: dict[str, list[float]] = defaultdict(list)
_err: dict[str, list[str]] = defaultdict(list)
_lock = threading.Lock()
completes = 0


def timed(kind: str, fn, *args, **kwargs):
    t = time.perf_counter()
    try:
        r = fn(*args, timeout=60, **kwargs)
        ok = r.status_code < 400
        detail = "" if ok else f"HTTP {r.status_code}"
    except requests.RequestException as exc:
        r, ok, detail = None, False, type(exc).__name__
    ms = (time.perf_counter() - t) * 1000
    with _lock:
        _lat[kind].append(ms)
        if not ok:
            _err[kind].append(detail)
    return r if ok else None


def respondent(base: str, token: str, i: int, think: float, stop: threading.Event) -> None:
    global completes
    s = requests.Session()
    while not stop.is_set():
        pid = f"L{i}-{random.random()}"
        entry = timed("respondent: open survey", s.get, f"{base}/r/{token}?pid={pid}")
        if entry is None:
            time.sleep(think)
            continue
        sid = entry.json().get("session_id")
        if not sid:  # held / closed
            return
        screened = random.random() < 0.2  # ~20% fail the screener, like real fieldwork
        for page in range(N_QUESTIONS):
            if stop.is_set():
                return
            time.sleep(random.uniform(think * 0.5, think * 1.5))
            answer = {f"Q{page}": (1 if page else (15 if screened else 35))}
            r = timed("respondent: submit page", s.post, f"{base}/r/{token}/sessions/{sid}/answers",
                      json={"answers": answer, "current_page": page + 1})
            if r is not None and r.json().get("terminated"):
                break
        else:
            if timed("respondent: complete", s.post,
                     f"{base}/r/{token}/sessions/{sid}/complete") is not None:
                with _lock:
                    completes += 1


def analyst(base: str, jwt: str, study: str, link_id: str, stop: threading.Event) -> None:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {jwt}"
    n = 0
    while not stop.is_set():
        timed("analyst: field monitor", s.get, f"{base}/studies/{study}/fielding/links")
        n += 1
        if n % 6 == 0:
            timed("analyst: field report (heavy)", s.get,
                  f"{base}/studies/{study}/fielding/links/{link_id}/field-report")
        if n % 15 == 0:
            timed("analyst: dry run (heavy)", s.post, f"{base}/studies/{study}/fielding/dry-run",
                  json={"n": 1000})
        time.sleep(random.uniform(4, 6))


SEED = r'''
from datetime import datetime
from api.db import Base, engine, SessionLocal, UserDB, StudyDB, QuestionnaireDB
from api.auth import hash_password
from src.core.survey_definition import *
Base.metadata.create_all(bind=engine)
s = SessionLocal(); now = datetime.now().isoformat()
qs = [Question(qid="Q0", text="Age", type=QuestionType.NUMERIC,
               terminate_if=Expression(op="lt", qid="Q0", value=18))]
qs += [Question(qid=f"Q{i}", text=f"Question {i}", type=QuestionType.SINGLE,
                options=[Option(code=c, label=f"option {c}") for c in range(1, 6)])
       for i in range(1, N_QUESTIONS)]
sv = SurveyDefinition(study_id="load", title="Load", source="authored", status="approved",
                      version=1, blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                      questions=qs)
if not s.query(UserDB).filter(UserDB.email == "load@local.test").first():
    s.add(UserDB(id="u_load", email="load@local.test", name="Load", role="owner",
                 is_active=True, created_at=now, password_hash=hash_password("LOAD_PASSWORD")))
if not s.query(StudyDB).filter(StudyDB.id == "load").first():
    s.add(StudyDB(id="load", client_name="Load test", status="classifying", created_at=now,
                  updated_at=now, study_metadata={}, approval_chain={}))
    s.add(QuestionnaireDB(id="q_load", study_id="load", status="approved",
                          source_filename="l.docx", version=1, definition_json=sv.to_json(),
                          definition_path=None, approved_qids=[q.qid for q in qs],
                          created_at=now, updated_at=now))
s.commit()
'''.replace("N_QUESTIONS", str(N_QUESTIONS))


CLEANUP = r'''
from api.db import (SessionLocal, StudyDB, QuestionnaireDB, UserDB, FieldingLinkDB,
                    RespondentSessionDB, RespondentAnswerDB)
s = SessionLocal()
links = s.query(FieldingLinkDB).filter(FieldingLinkDB.study_id == "load").all()
n = 0
for ln in links:
    for rs in s.query(RespondentSessionDB).filter(RespondentSessionDB.fielding_link_id == ln.id):
        s.query(RespondentAnswerDB).filter(RespondentAnswerDB.session_id == rs.id).delete()
        s.delete(rs); n += 1
    s.delete(ln)
s.query(QuestionnaireDB).filter(QuestionnaireDB.study_id == "load").delete()
s.query(StudyDB).filter(StudyDB.id == "load").delete()
s.query(UserDB).filter(UserDB.email == "load@local.test").delete()
s.commit()
print(f"removed the load-test project, {len(links)} links, {n} test responses and its owner")
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--study")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--seed-only", action="store_true",
                    help="on a server: create the load-test project and a one-off owner")
    ap.add_argument("--cleanup", action="store_true",
                    help="on a server: delete the load-test project, its responses and that owner")
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--analysts", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--think", type=float, default=3.0, help="avg seconds between pages")
    a = ap.parse_args()

    if a.seed_only:  # run inside the app container: ./dvv load-seed
        import secrets

        password = secrets.token_urlsafe(16)
        exec(SEED.replace("LOAD_PASSWORD", password))  # noqa: S102 - our own fixed code
        print("load-test owner: load@local.test")
        print(f"password (shown once): {password}")
        print("study: load   ->  delete it all afterwards with ./dvv load-cleanup")
        return
    if a.cleanup:
        exec(CLEANUP)  # noqa: S102 - our own fixed code
        return

    if requests is None:
        sys.exit("this needs the requests package: pip install requests")

    server = None
    if a.local:
        port = 8767
        tmp = tempfile.mkdtemp()
        env = {**os.environ, "PYTHONPATH": str(ROOT), "STUDIES_DIR": tmp,
               "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD", "load-admin"),
               "DATABASE_URL": os.environ.get("DATABASE_URL", f"sqlite:///{tmp}/load.db")}
        subprocess.run([sys.executable, "-c", SEED.replace("LOAD_PASSWORD", "load-pw")],
                       env=env, cwd=ROOT, check=True)
        log_path = Path(tmp) / "server.log"
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port),
             "--log-level", "warning"], env=env, cwd=ROOT,
            stdout=log_path.open("w"), stderr=subprocess.STDOUT)
        print(f"server log: {log_path}")
        a.base_url, a.email, a.password, a.study = (
            f"http://127.0.0.1:{port}", "load@local.test", "load-pw", "load")
        print(f"local server, database: {env['DATABASE_URL'].split('@')[-1]}")
    base = a.base_url.rstrip("/") + "/api"
    try:
        for _ in range(120):
            try:
                if requests.get(f"{base}/health", timeout=2).ok:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        login = {"username": a.email, "password": a.password}
        jwt = requests.post(f"{base}/auth/login", timeout=30, data=login).json()["access_token"]
        link = requests.post(f"{base}/studies/{a.study}/fielding/links", timeout=30,
                             headers={"Authorization": f"Bearer {jwt}"}, json={}).json()
        n_resp = a.users - a.analysts
        print(f"{n_resp} respondents + {a.analysts} analysts for {a.seconds}s "
              f"(a page every ~{a.think:.0f}s per respondent)...")
        stop = threading.Event()
        threads = [threading.Thread(target=respondent, args=(base, link["token"], i, a.think, stop))
                   for i in range(n_resp)]
        threads += [threading.Thread(target=analyst, args=(base, jwt, a.study, link["id"], stop))
                    for _ in range(a.analysts)]
        t0 = time.perf_counter()
        for th in threads:  # ramp over ~10 s, as real traffic does
            th.start()
            time.sleep(10 / len(threads))
        time.sleep(max(0, a.seconds - (time.perf_counter() - t0)))
        stop.set()
        for th in threads:
            th.join(timeout=90)
        elapsed = time.perf_counter() - t0

        total = sum(len(v) for v in _lat.values())
        errors = sum(len(v) for v in _err.values())
        print(f"\n{total} requests in {elapsed:.0f}s = {total / elapsed:.1f}/s · "
              f"errors {errors} ({100 * errors / max(total, 1):.2f}%) · completes {completes}")
        head = ("request", "count", "p50 ms", "p95 ms", "p99 ms", "errors")
        print(f"{head[0]:34} " + " ".join(f"{h:>8}" for h in head[1:]))
        for kind in sorted(_lat):
            v = sorted(_lat[kind])
            q = statistics.quantiles(v, n=100) if len(v) > 1 else [v[0]] * 99
            print(f"{kind:34} {len(v):8} {statistics.median(v):8.0f} {q[94]:8.0f} "
                  f"{q[98]:8.0f} {len(_err[kind]):8}")
        for kind, errs in _err.items():
            if errs:
                print(f"  {kind} failures: {sorted(set(errs))[:3]}")
    finally:
        if server:
            server.terminate()
            server.wait(timeout=30)


if __name__ == "__main__":
    main()
