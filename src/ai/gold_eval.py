"""The gold set: the Copilot asked about REAL projects, scored against answers
computed from their data.

The synthetic eval proves the Copilot works on a coffee survey we invented; it
cannot say how it does on a client's 600-column export, which is where every
serious bug this autumn was found. A gold case names a real project, asks what
an analyst would ask, and states what a right answer contains. Figures are not
typed in: each is computed from the project's own data file here, independently
of the Copilot, so the case stays right when the data is reprocessed.

The cases are client material: they live on the server (deploy/gold/, never in
git) and run there, through the full Copilot path, with conversations deleted
afterwards. Every case runs `repeat` times; a case that passes only sometimes is
FLAKY and reported as such — scores move between runs, and one run hides it.

Case file:
    {"cases": [{
        "id": "share-female", "skill": "figures",
        "study": {"source": "15841"},          # or {"id": "study_…"}
        "facts": {"female": {"pct": {"var": "S1", "codes": [2]}}},
        "turns": [{"say": "What share of respondents are female?",
                   "expect": {"mentions": ["{female}"]}}]}]}
Recipes: count, pct {var, codes, among?}, n_variables, top {var}, value "text".
`expect` takes the synthetic eval's keys (tools, tools_any, mentions, cards…).
"""

from __future__ import annotations

import re
import time
from typing import Any

from src.ai.copilot import TOOL_NAMES
from src.ai.copilot_eval import EXPECT_KEYS, number_pattern, score_case

SKILLS = ("routing", "figures", "tables", "data", "questionnaire", "files", "clarify",
          "safety", "fieldwork")


def problems(cases: list[dict]) -> list[str]:
    found = []
    for c in cases:
        cid = c.get("id", "?")
        if not c.get("turns"):
            found.append(f"{cid}: no turns")
        if c.get("skill") not in SKILLS:
            found.append(f"{cid}: skill must be one of {', '.join(SKILLS)}")
        if not (c.get("study") or {}).get("id") and not (c.get("study") or {}).get("source"):
            found.append(f"{cid}: study needs an id or a source filename fragment")
        for turn in c.get("turns") or []:
            expect = turn.get("expect") or {}
            if unknown := set(expect) - EXPECT_KEYS:
                found.append(f"{cid}: unknown expectation {sorted(unknown)}")
            for key in ("tools", "tools_any", "no_tools"):
                if bad := [t for t in expect.get(key, []) if t not in TOOL_NAMES]:
                    found.append(f"{cid}: no tool {bad}")
    ids = [c.get("id") for c in cases]
    found += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return found


def resolve_study(db, ref: dict):
    from api.db import StudyDB

    if ref.get("id"):
        return db.get(StudyDB, ref["id"])
    rows = [s for s in db.query(StudyDB).all()
            if ref["source"].lower() in (s.source_filename or "").lower()]
    rows.sort(key=lambda s: s.updated_at or "", reverse=True)  # the latest upload of it
    return rows[0] if rows else None


def compute_facts(study, recipes: dict[str, Any]) -> dict[str, str]:
    """name -> regex of the right answer, computed from the study's own data."""
    import polars as pl

    from api.routes.chat import _load_datamap, _study_dir

    if not recipes:
        return {}
    d = _study_dir(study)
    dm = _load_datamap(d, study)
    df = pl.read_parquet(d / "data.parquet") if (d / "data.parquet").exists() else None
    out = {}
    for name, recipe in recipes.items():
        (kind, arg), = recipe.items()
        if kind == "value":
            out[name] = re.escape(str(arg))
        elif kind == "n_variables":
            out[name] = number_pattern(len(dm))
        elif kind == "count":
            out[name] = number_pattern(df.height)
        elif kind == "pct":
            base = df.filter(pl.col(arg["var"]).is_not_null())
            for var, codes in (arg.get("among") or {}).items():
                base = base.filter(pl.col(var).is_in(codes))
            hit = base.filter(pl.col(arg["var"]).is_in(arg["codes"])).height
            out[name] = number_pattern(100 * hit / base.height if base.height else 0)
        elif kind == "top":
            counts = df[arg["var"]].drop_nulls().value_counts(sort=True)
            code = counts[arg["var"]][0]
            out[name] = re.escape(dm[arg["var"]].values.get(int(code), str(code)))
        else:
            raise ValueError(f"unknown recipe {kind!r} in fact {name!r}")
    return out


def run_case(db, case: dict, user) -> tuple[list[dict], dict[str, str]]:
    """One pass through a case's turns, on the real Copilot path; the conversation
    is deleted afterwards so the gold set leaves nothing in anyone's history."""
    from api.db import delete_copilot_conversation, get_copilot_conversation
    from api.models import CopilotRequest
    from api.routes.copilot import _answer

    study = resolve_study(db, case["study"])
    if study is None:
        return [{"error": f"no project matches {case['study']}"}], {}
    facts = compute_facts(study, case.get("facts") or {})
    responses, conversation = [], None
    try:
        for turn in case["turns"]:
            started = time.perf_counter()
            body = CopilotRequest(message=turn["say"], study_id=study.id,
                                  conversation_id=conversation, screen=turn.get("screen"))
            try:
                answer = _answer(body, db, user).model_dump()
            except Exception as exc:  # noqa: BLE001 - a failed answer is a failed case
                answer = {"error": str(getattr(exc, "detail", exc))}
            answer["seconds"] = round(time.perf_counter() - started, 1)
            responses.append(answer)
            conversation = answer.get("conversation_id") or conversation
    finally:
        if conversation and (c := get_copilot_conversation(db, conversation, user.id)):
            delete_copilot_conversation(db, c)
    return responses, facts


def run(db, cases: list[dict], user, repeat: int = 3, say=print) -> dict:
    """Every case `repeat` times. A case counts as passed only if every repeat
    passed; flaky when some did. Scores per skill are pass rates over repeats."""
    results, flaky, per_skill = [], [], {}
    for case in cases:
        case = {**case, "category": case.get("skill", "routing")}
        passes = []
        last = None
        for _ in range(repeat):
            responses, facts = run_case(db, case, user)
            last = score_case(case, responses, facts)
            passes.append(last["passed"])
        stable = all(passes)
        if any(passes) and not stable:
            flaky.append(case["id"])
        per_skill.setdefault(case["category"], []).extend(passes)
        mark = "PASS " if stable else ("FLAKY" if any(passes) else "FAIL ")
        say(f"{mark} {case['id']:<34} {sum(passes)}/{repeat}  {case['category']}")
        if not stable:
            for f in last["failures"]:
                say(f"        - {f}")
            for tools, reply in zip(last["tools_used"], last["replies"], strict=False):
                say(f"        tools {tools}: {' '.join(reply.split())[:240]}")
        results.append({**last, "passed": stable, "passes": passes})
    n = len(results)
    summary = {
        "cases": n, "passed": sum(r["passed"] for r in results),
        "score": round(100 * sum(r["passed"] for r in results) / max(n, 1), 1),
        "by_category": {k: f"{round(100 * sum(v) / len(v))}% ({sum(v)}/{len(v)})"
                        for k, v in sorted(per_skill.items())},
        "median_seconds": None,
    }
    times = sorted(t for r in results for t in r["seconds"] if t is not None)
    if times:
        summary["median_seconds"] = times[len(times) // 2]
    return {"summary": summary, "results": results, "flaky": flaky}
