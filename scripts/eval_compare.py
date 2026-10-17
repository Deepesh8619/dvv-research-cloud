#!/usr/bin/env python3
# ruff: noqa: E501
"""Compare an independent reading of a questionnaire (gold) with what we build.

    python scripts/eval_compare.py --docx Q.docx --gold gold/q.json --out DIR [--baseline prev]
    python scripts/eval_compare.py --batch DIR [DIR ...] --out DIR [--baseline prevdir]

Three layers per gold question (schema: DVV/eval/GOLD_SCHEMA.md):
  1. qnre vs PARSER  - the SurveyDefinition the offline extractor produces.
  2. qnre vs LINK    - the SurveyJS survey the compiler hands a respondent,
                       including BEHAVIOUR: sampled answer worlds through gold
                       show_if vs parser Expression vs compiled visibleIf (the
                       real survey-core ConditionRunner, run under node).
  3. DEFECTS         - was each document fault flagged on that qid?

The parser run is AI OFF. This module sets the keys empty BEFORE importing src
(src/env.py only setdefaults, so empty stays empty) and refuses to read any
document unless llm_key_presence() is all False. Outputs hold client content:
they are written only under --out, which may not be inside the repo.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import re
import subprocess
import sys
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("/Users/deepeshsharma/dev/DVV/eval/rounds")
_AI_OFF = {
    "GROQ_API_KEY": "", "CEREBRAS_API_KEY": "", "BEDROCK_API_KEY": "",
    "HF_TOKEN": "", "OLLAMA_HOST": "http://127.0.0.1:1",
}
os.environ.update(_AI_OFF)
os.environ.pop("DVV_EXTRACT_LOCAL", None)
sys.path.insert(0, str(REPO))

from src.ai.router import BackendUnavailable, llm_key_presence  # noqa: E402
from src.core.survey_definition import (  # noqa: E402
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
    selected_codes,
)
from src.engines.fielding.preflight import preflight  # noqa: E402
from src.engines.questionnaire.definition_audit import audit_definition  # noqa: E402
from src.engines.questionnaire.extractor import extract_survey_definition  # noqa: E402
from src.engines.questionnaire.write_ins import write_in_name  # noqa: E402
from src.intake.questionnaire_loader import load_docx, segment_into_questions  # noqa: E402
from src.renderers.surveyjs_compiler import compile_to_surveyjs  # noqa: E402

QT = QuestionType
WORLDS = 200
SEVERITY = ["MISSING", "STRUCK_FIELDED", "TYPE_DIFF", "OPTION_DIFF", "LOGIC_DIFF",
            "CONSTRAINT_DIFF", "TEXT_DIFF", "QID_SHIFT"]
GOLD_TYPES: dict[str, set | None] = {
    "single": {QT.SINGLE}, "multi": {QT.MULTI}, "numeric": {QT.NUMERIC},
    "text": {QT.OPEN_TEXT}, "dropdown": {QT.DROPDOWN, QT.SINGLE}, "ranking": {QT.RANK},
    "constant_sum": {QT.NUMERIC}, "grid_single": {QT.GRID_SINGLE},
    "grid_multi": set(),  # the model has no multi-answer grid: always a TYPE_DIFF
    "maxdiff": {QT.MAXDIFF}, "date": {QT.OPEN_TEXT, QT.NUMERIC},
    "slider": {QT.NUMERIC, QT.SINGLE}, "other": None,
}
LINK_KIND = {"radiogroup": "single", "rating": "single", "checkbox": "multi",
             "dropdown": "dropdown", "ranking": "ranking", "matrix": "grid",
             "multipletext": "numeric", "html": "html"}
LINK_OK = {"single": {"single"}, "multi": {"multi"}, "dropdown": {"dropdown", "single"},
           "ranking": {"ranking"}, "numeric": {"numeric"}, "constant_sum": {"numeric"},
           "text": {"text"}, "grid_single": {"grid"}, "grid_multi": set(),
           "date": {"text", "numeric"}, "slider": {"numeric", "single"}}
DEFECT_WORDS = {
    "unknown_reference": ["unknown", "reference", "refers", "undefined", "not found", "unresolved"],
    "code_gap": ["gap", "skipped", "missing code", "consecutive", "non-sequential"],
    "duplicate_code": ["duplicate", "twice", "repeated", "same code"],
    "duplicate_qid": ["duplicate", "renamed", "same id", "already used"],
    "missing_code": ["missing", "no code", "unnumbered", "without a code"],
    "contradiction": ["contradict", "conflict", "inconsistent", "impossible", "never"],
    "typo": ["typo", "spelling", "misspell"],
    "unmapped_value": ["unmapped", "not mapped", "no mapping", "derive", "computed", "hidden"],
    "wording_vs_type": ["wording", "type", "each", "select up to", "select all", "per item"],
}


ALIAS: dict[str, str] = {}  # set per document by run_document (single-threaded)


class Refuse(SystemExit):
    pass


def assert_ai_off() -> None:
    os.environ.update(_AI_OFF)
    present = llm_key_presence()
    if any(present.values()) or os.environ.get("OLLAMA_HOST") != _AI_OFF["OLLAMA_HOST"]:
        raise Refuse(f"REFUSING to read a document: AI is not off ({present})")


# ---------------------------------------------------------------- text helpers
_BR = re.compile(r"\[[^\]]*\]|\{[^}]*\}")


def nq(qid: Any) -> str:
    return re.sub(r"[^A-Z0-9_.]", "", str(qid).upper()).rstrip(".")


def nt(text: Any) -> str:
    t = _BR.sub(" ", str(text or "")).casefold()
    t = re.sub(r"^\s*[a-z]{1,4}\d+[a-z]?[.:)]\s*", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def same_text(a: Any, b: Any) -> bool:
    x, y = nt(a), nt(b)
    if x == y:
        return True
    if min(len(x), len(y)) >= 10 and (x in y or y in x):
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.85


def clip(s: Any, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", str(s)).replace("|", "/")
    return s if len(s) <= n else s[: n - 1] + "…"


def opts_str(opts: list[dict]) -> str:
    bits = [f"{o['code']}={clip(o.get('label'), 40)}"
            + ("[X]" if o.get("exclusive") else "") + ("[A]" if o.get("anchored") else "")
            for o in opts[:8]]
    return ", ".join(bits) + (f" (+{len(opts) - 8})" if len(opts) > 8 else "")


# ---------------------------------------------------------------- expressions
def gold_str(e: Any) -> str:
    if not e:
        return "none"
    if "op" in e:
        return f"{e['op']}(" + ", ".join(gold_str(a) for a in e.get("args", [])) + ")"
    return " ".join(f"{k}={v}" for k, v in e.items())


def expr_str(e: Expression | None) -> str:
    if e is None:
        return "none"
    if e.children is not None:
        return f"{e.op}(" + ", ".join(expr_str(c) for c in e.children) + ")"
    bit = f"{e.qid}{'.' + str(e.row) if e.row is not None else ''} {e.op} "
    return bit + (str(e.values) if e.values is not None else str(e.value))


def gold_refs(e: Any) -> set[str]:
    if not e:
        return set()
    out = {nq(e["qid"])} if "qid" in e else set()
    for a in e.get("args", []):
        out |= gold_refs(a)
    return out


def gold_consts(e: Any) -> set[float]:
    out: set[float] = set()
    if not e:
        return out
    for k in ("in", "not_in", "any_selected", "none_selected"):
        out |= {float(v) for v in e.get(k, []) if isinstance(v, (int, float))}
    for k in ("gte", "lte", "eq"):
        if isinstance(e.get(k), (int, float)):
            out.add(float(e[k]))
    for a in e.get("args", []):
        out |= gold_consts(a)
    return out


def parser_consts(e: Expression | None) -> set[float]:
    if e is None:
        return set()
    vals = list(e.values or []) + [e.value]
    out = {float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for c in e.children or []:
        out |= parser_consts(c)
    return out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def gold_eval(e: dict, w: dict[str, Any]) -> bool:
    op = e.get("op")
    if op == "and":
        return all(gold_eval(a, w) for a in e["args"])
    if op == "or":
        return any(gold_eval(a, w) for a in e["args"])
    if op == "not":
        return not any(gold_eval(a, w) for a in e["args"])
    v = w.get(nq(e["qid"]))
    if "row" in e and isinstance(v, dict):
        v = v.get(e["row"], v.get(str(e["row"])))
    if "answered" in e:
        return (v not in (None, [], {})) == bool(e["answered"])
    if v is None:
        return False  # unanswered: the condition is not met (same as the repo's evaluate)
    chosen = selected_codes(v)
    if "in" in e or "any_selected" in e:
        return bool(chosen & set(e.get("in", e.get("any_selected"))))
    if "not_in" in e:
        return not (chosen & set(e["not_in"]))
    if "none_selected" in e:
        return not (chosen & set(e["none_selected"]))
    n = _num(v)
    if n is None:
        return False
    return {"gte": lambda: n >= e["gte"], "lte": lambda: n <= e["lte"],
            "eq": lambda: n == e["eq"]}[next(k for k in ("gte", "lte", "eq") if k in e)]()


_REF = re.compile(r"\{([A-Za-z0-9_]+)((?:\.[^}]*)?)\}")


_NODE_JS = r"""
const {Model}=require('survey-core');
const j=JSON.parse(require('fs').readFileSync(0,'utf8'));
const val=(m,n,v)=>{const q=m.getQuestionByName(n);if(q&&['checkbox','tagbox','ranking'].includes(q.getType())&&!Array.isArray(v))return [v];return v};
const shown=q=>!!q&&q.isVisible&&(!q.page||q.page.isVisible);
console.log(JSON.stringify(j.jobs.map(x=>{
  const m=new Model(j.survey);const t=m.getQuestionByName(x.target);
  if(!t)return x.kind==='shown'?x.worlds.map(()=>null):null;
  try{
    if(x.kind==='shown')return x.worlds.map(w=>{m.clear();for(const[k,v]of Object.entries(w))m.setValue(k,val(m,k,v));return shown(t)});
    m.clear();for(const[k,v]of Object.entries(x.answers||{}))m.setValue(k,val(m,k,v));
    if(x.kind==='choices')return (t.visibleChoices||t.visibleRows||[]).length;
    if(x.kind==='error'){t.value=x.value;return t.hasErrors(false)}
  }catch(e){return null}
})))
"""


def node_run(survey: dict, jobs: list[dict]) -> list | None:
    """Run the compiled survey in SurveyJS itself (survey-core under node), headless.

    Falls back to None when node/survey-core is missing; callers then say so."""
    lib = REPO / "web" / "node_modules"
    if not (lib / "survey-core").exists() or not jobs:
        return None
    try:
        p = subprocess.run(["node", "-e", _NODE_JS], input=json.dumps({"survey": survey, "jobs": jobs}),
                           capture_output=True, text=True, timeout=300, env={**os.environ, "NODE_PATH": str(lib)})
        return json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


# ---------------------------------------------------------------- matching
def _opt_overlap(g: dict, q: Question) -> float:
    gl = {nt(o.get("label")) for o in g.get("options") or g.get("rows") or []} - {""}
    pl = {nt(o.label) for o in q.options or q.rows or []} - {""}
    return len(gl & pl) / len(gl | pl) if gl and pl else 0.0


def match_questions(gold_qs: list[dict], pqs: list[Question]) -> dict[int, tuple[Question, str]]:
    """Pair gold with parser questions by WORDING first (+ option overlap); qid only confirms.

    Word auto-numbering makes parser qids off by one from the visible numbers, so a qid-first
    match would pair the wrong questions. A pair whose qids differ is reported as QID_SHIFT
    by the caller (how == 'text')."""
    out: dict[int, tuple[Question, str]] = {}
    used: set[int] = set()
    ng, npq = max(len(gold_qs), 1), max(len(pqs), 1)
    cands = []
    for i, g in enumerate(gold_qs):
        gt = nt(g.get("text"))
        if not gt:
            continue
        for j, q in enumerate(pqs):
            pt = nt(q.text)
            if not pt:
                continue
            r = difflib.SequenceMatcher(None, gt, pt).ratio()
            if min(len(gt), len(pt)) >= 10 and (gt in pt or pt in gt):
                r = max(r, 0.9)
            score = 0.8 * r + 0.2 * _opt_overlap(g, q) - 0.05 * abs(i / ng - j / npq)
            if nq(g["qid"]) == nq(q.qid):
                score += 0.1
            if r >= 0.6:
                cands.append((score, r, i, j))
    for _score, _r, i, j in sorted(cands, key=lambda c: (-c[0], c[2], c[3])):
        if i in out or id(pqs[j]) in used:
            continue
        used.add(id(pqs[j]))
        same = nq(gold_qs[i]["qid"]) == nq(pqs[j].qid)
        out[i] = (pqs[j], "text+qid" if same else "text")
    # whatever is left: qid equality (repeated gold qids map to Q_2, Q_3), when the
    # wording does not flatly contradict it
    by_norm = {nq(q.qid): q for q in reversed(pqs) if id(q) not in used}
    seen: dict[str, int] = defaultdict(int)
    for i, g in enumerate(gold_qs):
        key = nq(g["qid"])
        seen[key] += 1
        if i in out:
            continue
        q = by_norm.get(key if seen[key] == 1 else f"{key}_{seen[key]}")
        if q is None or id(q) in used:
            continue
        gt, pt = nt(g.get("text")), nt(q.text)
        if gt and pt and difflib.SequenceMatcher(None, gt, pt).ratio() < 0.35:
            continue
        used.add(id(q))
        out[i] = (q, "qid" if seen[key] == 1 else "renamed_dup")
    return out


# ---------------------------------------------------------------- comparisons
class Diff(dict):
    def __init__(self, verdict: str, kind: str, detail: str):
        super().__init__(verdict=verdict, kind=kind, detail=detail)


def cmp_options(gold: list[dict], got: list[dict], what: str, flags: bool) -> list[Diff]:
    gm = {str(o["code"]): o for o in gold}
    pm = {str(o["code"]): o for o in got}
    out: list[Diff] = []
    if miss := [c for c in gm if c not in pm]:
        out.append(Diff("OPTION_DIFF", f"{what}_code_missing", f"{what} codes missing: {miss}"))
    if extra := [c for c in pm if c not in gm]:
        out.append(Diff("OPTION_DIFF", f"{what}_code_extra", f"{what} codes added: {extra}"))
    bad = [f"{c}: ASKED '{clip(gm[c].get('label'), 50)}' GOT '{clip(pm[c].get('label'), 50)}'"
           for c in gm if c in pm and not same_text(gm[c].get("label"), pm[c].get("label"))]
    if bad:
        out.append(Diff("OPTION_DIFF", f"{what}_label", f"{what} labels differ: " + "; ".join(bad[:5])))
    if flags:
        for gk, pk, name in (("exclusive", "exclusive", "exclusive"), ("anchored", "anchored", "anchored"),
                             ("other_specify", "other", "other_specify")):
            d = [c for c in gm if c in pm and bool(gm[c].get(gk)) != bool(pm[c].get(pk))]
            if d:
                out.append(Diff("OPTION_DIFF", f"{what}_{name}", f"{what} {name} differs on codes {d}"))
    return out


def p_opts(opts: list | None) -> list[dict]:
    return [{"code": o.code, "label": o.label, "exclusive": o.exclusive,
             "anchored": o.anchored, "other": o.open_other} for o in opts or []]


def l_opts(choices: list | None) -> list[dict]:
    return [{"code": c.get("value", c) if isinstance(c, dict) else c,
             "label": c.get("text", "") if isinstance(c, dict) else ""} for c in choices or []]


def same_num(a: Any, b: Any) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and float(a) == float(b))


def cmp_constraints(g: dict, got: dict[str, Any], who: str) -> list[Diff]:
    out = []
    gc = g.get("constraints") or {}
    for gk, pk in (("min", "min"), ("max", "max"), ("sum", "sum"),
                   ("select_min", "select_min"), ("select_max", "select_max"), ("digits", "digits")):
        if pk not in got:
            continue  # this layer cannot express it; nothing to compare
        if not same_num(gc.get(gk), got[pk]):
            out.append(Diff("CONSTRAINT_DIFF", f"constraint_{gk}",
                            f"{gk}: ASKED {gc.get(gk)} {who} GOT {got[pk]}"))
    if gc.get("required") is not None and "required" in got and bool(gc["required"]) != bool(got["required"]):
        out.append(Diff("CONSTRAINT_DIFF", "constraint_required",
                        f"required: ASKED {gc['required']} {who} GOT {got['required']}"))
    return out


def parser_limits(q: Question) -> dict[str, Any]:
    v = q.validation or {}
    return {"min": v.get("min"), "max": v.get("max"), "sum": v.get("sum"),
            "select_min": v.get("min_select"), "select_max": v.get("max_select"),
            "required": q.required, **({"digits": v["digits"]} if "digits" in v else {})}


def link_limits(el: dict) -> dict[str, Any]:
    lim: dict[str, Any] = {"min": None, "max": None, "sum": None, "required": el.get("isRequired"),
                           "select_min": el.get("minSelectedChoices"),
                           "select_max": el.get("maxSelectedChoices")}
    vals = list(el.get("validators") or [])
    for it in el.get("items") or []:
        vals += it.get("validators") or []
    for v in vals:
        if v.get("type") == "numeric":
            lim["min"], lim["max"] = v.get("minValue"), v.get("maxValue")
        elif v.get("type") == "expression":
            ex = v.get("expression", "")
            if m := re.search(r">=\s*(-?\d+(?:\.\d+)?)\s*(?:and|\)|$)", ex):
                lim["min"] = float(m.group(1)) if lim["min"] is None else lim["min"]
            if m := re.search(r"<=\s*(-?\d+(?:\.\d+)?)", ex):
                lim["max"] = float(m.group(1))
            if el.get("type") == "multipletext" and (m := re.search(r"\}\s*=\s*(-?\d+(?:\.\d+)?)\s*$", ex)):
                lim["sum"] = float(m.group(1))
    return lim


def pq_techniques(q: Question) -> dict:
    return (q.validation or {}).get("techniques") or {}


def cmp_list_pipes(g: dict, q: Question) -> list[Diff]:
    out: list[Diff] = []
    t = pq_techniques(q)
    masks = t.get("masks") or []
    pipes = {nq(p.get("source_qid")) for p in t.get("pipes") or [] if p.get("source_qid")}
    pipes |= {nq(x) for x in q.piping_refs}
    ls = g.get("list_source")
    if ls:
        want = {"all": {"full_list"}, "selected": {"selected"},
                "not_selected": {"not_selected", "list_except"}}.get(ls.get("mode"), set())
        ok = any(nq(m.get("source_qid") or m.get("exclude_qid")) in
                 {nq(ls.get("from_qid")), nq(ls.get("exclude_qid"))}
                 and m.get("mode") in want for m in masks)
        if not ok and not (ls.get("mode") == "all" and not masks):
            out.append(Diff("OPTION_DIFF", "list_source",
                            f"list_source: ASKED {ls.get('mode')} of {ls.get('from_qid')} "
                            f"GOT {[(m.get('mode'), m.get('source_qid')) for m in masks] or 'none'}"))
    elif any(m.get("mode") != "full_list" for m in masks):
        out.append(Diff("OPTION_DIFF", "list_source_extra",
                        f"parser narrows the list from {[m.get('source_qid') for m in masks]}; "
                        f"the document does not"))
    for p in g.get("pipes") or []:
        if p.get("into") == "options":
            continue
        if nq(p["source_qid"]) not in pipes:
            out.append(Diff("TEXT_DIFF", "pipe_missing", f"pipe {{{p['source_qid']}}} not recognised"))
    return out


class World:
    """Answer worlds over the questions an expression set refers to."""

    def __init__(self, seed: str, names: set[str], gold_by: dict, parser_by: dict, nums: set[float]):
        rng = random.Random(zlib.crc32(seed.encode()))
        dom: dict[str, tuple] = {}
        for n in sorted(names):
            g, p = gold_by.get(n), parser_by.get(n)
            t = g["type"] if g else (p.type.value if p else None)
            if g:
                codes = [o["code"] for o in g.get("options") or []]
                rows = [r["code"] for r in g.get("rows") or []]
                cols = [c["code"] for c in g.get("columns") or []]
            elif p:
                codes = [o.code for o in p.options]
                rows = [r.code for r in p.rows or []]
                cols = [c.code for c in p.cols or []]
            else:
                codes = rows = cols = []
            if t in ("multi",):
                dom[n] = ("multi", codes or sorted(int(x) for x in nums))
            elif t in ("grid_single", "grid_multi") and rows and cols:
                dom[n] = ("grid", rows, cols)
            elif t in ("numeric", "slider", "constant_sum", "date") or not codes:
                pool = sorted({v for x in nums for v in (x - 1, x, x + 1)} | {0.0})
                dom[n] = ("num", [int(v) if float(v).is_integer() else v for v in pool])
            else:
                dom[n] = ("one", codes)
        self.worlds = []
        for _ in range(WORLDS):
            w: dict[str, Any] = {}
            for n, d in dom.items():
                if d[0] == "multi":
                    w[n] = rng.sample(d[1], rng.randint(0, min(len(d[1]), 3)))
                elif d[0] == "grid":
                    w[n] = {r: rng.choice(d[2]) for r in d[1]}
                elif d[0] == "num":
                    w[n] = rng.choice(d[1] + [rng.randint(0, 100)])
                else:
                    w[n] = rng.choice(d[1])
            self.worlds.append(w)

    def view(self, w: dict, names: set[str]) -> dict:
        return {n: w[nq(n)] for n in names if nq(n) in w}


_ROWREF = re.compile(r"^(.+?)(?:_[rR]|\.)(\d+)$")


def rows_to_base(e: Any, gold_by: dict) -> Any:
    """Gold may address one grid row as qid GRID_r3: rewrite to qid GRID + row 3."""
    if not isinstance(e, dict):
        return e
    if "args" in e:
        return {**e, "args": [rows_to_base(a, gold_by) for a in e["args"]]}
    m = _ROWREF.match(str(e.get("qid", "")))
    if m and nq(e["qid"]) not in gold_by and nq(m[1]) in gold_by:
        return {**e, "qid": m[1], "row": int(m[2])}
    return e


def show(w: dict) -> str:
    return "{" + ", ".join(f"{k}: {v}" for k, v in sorted(w.items())) + "}"


def behaviour(label: str, seed: str, gold_e: Any, parser_e: Expression | None,
              link_s: str | None, gold_by: dict, parser_by: dict,
              link: tuple[dict, str] | None) -> tuple[list[Diff], list[Diff]]:
    """(parser diffs, link diffs) for one condition across sampled worlds.

    link = (compiled survey, target qid): the LINK side is the real survey-core
    model, visibility read as isVisible on the question AND its page."""
    gold_e = rows_to_base(gold_e, gold_by)
    al = lambda n: ALIAS.get(nq(n), nq(n))  # noqa: E731 - parser qid -> the document's visible qid
    if gold_e is None and parser_e is None and not link_s:
        return [], []
    link_names = {m.group(1) for m in _REF.finditer(link_s or "")}
    names = (gold_refs(gold_e) | {al(x) for x in (parser_e.referenced_qids() if parser_e else set())}
             | {al(x) for x in link_names})
    nums = gold_consts(gold_e) | parser_consts(parser_e) | {float(x) for x in re.findall(r"\b\d+\b", link_s or "")}
    wd = World(seed, names, gold_by, parser_by, nums)
    gold_res = [gold_eval(gold_e, w) if gold_e else True for w in wd.worlds]

    def counter(res: list, who: str, kind: str) -> list[Diff]:
        bad = [i for i, (a, b) in enumerate(zip(gold_res, res, strict=True)) if b is not None and a != b]
        if not bad:
            return []
        i = bad[0]
        return [Diff("LOGIC_DIFF", f"{label}_{kind}",
                     f"{label} differs in {len(bad)}/{WORLDS} worlds, e.g. {show(wd.worlds[i])}: "
                     f"ASKED {'shown' if gold_res[i] else 'hidden'} vs {who} "
                     f"{'shown' if res[i] else 'hidden'}  [gold {gold_str(gold_e)} | {who} "
                     f"{expr_str(parser_e) if who == 'PARSER' else link_s}]")]

    p_res = [evaluate(parser_e, {n: w[al(n)] for n in parser_e.referenced_qids() if al(n) in w}) if parser_e else True
             for w in wd.worlds]
    pd = counter(p_res, "PARSER", _shape(gold_e, parser_e))
    ld: list[Diff] = []
    if link:
        views = [{n: w[al(n)] for n in link_names if al(n) in w} for w in wd.worlds]
        res = node_run(link[0], [{"kind": "shown", "target": link[1], "worlds": views}])
        if res is None or res[0] is None:
            ld = [Diff("LOGIC_DIFF", f"{label}_unchecked", "link NOT run in SurveyJS (node/survey-core unavailable)")]
        else:
            ld = counter(res[0], "LINK", _shape(gold_e, parser_e))
    return pd, ld


def _shape(g: Any, p: Any) -> str:
    return "gold_base_parser_none" if g and not p else "parser_base_gold_none" if p and not g else "bases_differ"


def gold_terminate(g: dict) -> dict | None:
    parts = [t["when"] for t in g.get("terminate_if") or [] if t.get("when")]
    codes = [o["code"] for o in g.get("options") or [] if o.get("terminate")]
    if codes:
        parts.append({"qid": g["qid"], "any_selected" if g["type"] == "multi" else "in": codes})
    return None if not parts else parts[0] if len(parts) == 1 else {"op": "or", "args": parts}


# ---------------------------------------------------------------- the link
def link_index(compiled: dict) -> dict[str, tuple[dict, dict]]:
    out = {}
    for page in compiled.get("pages", []):
        for el in page.get("elements", []):
            if el.get("name"):
                out.setdefault(nq(el["name"]), (el, page))
    return out


def link_visible(el: dict, page: dict) -> str | None:
    parts = [x for x in (page.get("visibleIf"), el.get("visibleIf")) if x]
    return " and ".join(f"({x})" for x in parts) or None


def link_got(el: dict | None) -> dict:
    if el is None:
        return {"present": False}
    ch = el.get("choices") or el.get("rateValues") or []
    return {"present": True, "element": el["type"], "title": el.get("title"),
            "choices": l_opts(ch), "rows": l_opts(el.get("rows") or el.get("items")),
            "columns": l_opts(el.get("columns")), "visibleIf": el.get("visibleIf"),
            "limits": link_limits(el)}


def cmp_link(g: dict, el: dict | None, page: dict | None, q: Question | None,
             links: dict | None = None) -> list[Diff]:
    gold_type = g["type"]
    if not g.get("asked", True):
        if el is None:
            return []
        return [Diff("STRUCK_FIELDED" if g.get("struck_out") else "TYPE_DIFF", "fielded_not_asked",
                     "fielded to the respondent but the document does not ask it")]
    if g.get("struck_out"):
        return [Diff("STRUCK_FIELDED", "struck_fielded", "struck-out question is fielded")] if el else []
    if el is None:
        why = "parser has it but the link does not show it" if q else "absent"
        return [Diff("MISSING", "link_missing", f"no element in the compiled survey ({why})")]
    out: list[Diff] = []
    kind = LINK_KIND.get(el["type"], el["type"])
    if el["type"] == "text" and el.get("inputType") == "number":
        kind = "numeric"
    elif el["type"] in ("text", "comment"):
        kind = "text"
    if gold_type in LINK_OK and kind not in LINK_OK[gold_type]:
        out.append(Diff("TYPE_DIFF", f"link_type {gold_type}->{kind}", f"type: ASKED {gold_type} LINK {el['type']}"))
        return out
    if not same_text(g.get("text"), re.sub(r"\{[^}]*\}", "", el.get("title") or "")) and \
            not same_text(re.sub(r"\{[^}]*\}", "", g.get("text") or ""), re.sub(r"\{[^}]*\}", "", el.get("title") or "")):
        out.append(Diff("TEXT_DIFF", "link_title", f"title: ASKED '{clip(g.get('text'), 90)}' GOT '{clip(el.get('title'), 90)}'"))
    if any(p.get("into") != "options" for p in g.get("pipes") or []) and "{" not in (el.get("title") or ""):
        out.append(Diff("TEXT_DIFF", "link_pipe_missing", "gold pipes an earlier answer into the text; the link title has no {marker}"))
    rating = el["type"] == "rating"
    opts = l_opts(el.get("choices") or el.get("rateValues"))
    if g.get("options") and el["type"] != "multipletext":
        gold_o = g["options"]
        if rating:
            gold_o = [{"code": o["code"]} for o in gold_o]
            opts = [{"code": o["code"]} for o in opts]
        out += cmp_options(gold_o, opts, "option", False) if not rating else \
            [d for d in cmp_options([{"code": o["code"], "label": ""} for o in gold_o],
                                    [{"code": o["code"], "label": ""} for o in opts], "option", False)]
    if g.get("rows"):
        out += cmp_options(g["rows"], l_opts(el.get("rows") or el.get("items")), "row", False)
    if g.get("columns"):
        out += cmp_options(g["columns"], l_opts(el.get("columns")), "column", False)
    # "Other (specify)": the respondent must get a text box that appears when it is picked.
    for o in (g.get("options") or []) + (g.get("rows") or []):
        if o.get("other_specify") and q is not None and links is not None:
            box = links.get(nq(write_in_name(q.qid, o["code"])))
            if box is None or box[0].get("type") != "text" or not box[0].get("visibleIf"):
                out.append(Diff("OPTION_DIFF", "link_other_specify",
                                f"option {o['code']} asks the respondent to specify; the link has no text box that opens when it is picked"))
    lim = link_limits(el)
    lim = {k: v for k, v in lim.items() if k in ("min", "max", "sum", "select_min", "select_max", "required")}
    out += cmp_constraints(g, lim, "LINK")
    if g.get("list_source") and g["list_source"].get("mode") != "all" and not (
            el.get("choicesVisibleIf") or el.get("rowsVisibleIf")):
        out.append(Diff("OPTION_DIFF", "link_list_source", "list narrowed by an earlier answer in the document; the link shows the full list"))
    return out


def probe_diffs(g: dict, el: dict, compiled: dict, gold_by: dict) -> list[Diff]:
    """Ask the real SurveyJS model: does a list narrow, and does a wrong total get refused?"""
    out: list[Diff] = []
    ls, name = g.get("list_source"), el["name"]
    src = gold_by.get(nq(ls["from_qid"])) if ls and ls.get("from_qid") else None
    own = {str(o["code"]) for o in g.get("options") or g.get("rows") or []}
    if ls and ls.get("mode") == "selected" and src and src.get("options") and len(own) > 2:
        pick_codes = [o["code"] for o in src["options"] if str(o["code"]) in own][:2]
        if len(pick_codes) == 2:
            ans = {src["qid"]: pick_codes if src["type"] == "multi" else pick_codes[0]}
            r = node_run(compiled, [{"kind": "choices", "target": name, "answers": ans}])
            n = r[0] if r else None
            if isinstance(n, int) and n >= len(own):
                out.append(Diff("OPTION_DIFF", "link_mask_not_narrowing",
                                f"after selecting {pick_codes} at {src['qid']} the link still offers {n} of {len(own)} answers"))
    total = (g.get("constraints") or {}).get("sum")
    if isinstance(total, (int, float)) and g.get("rows") and el.get("type") == "multipletext":
        rows = [r["code"] for r in g["rows"]]
        def spread(t: float) -> dict:
            return {str(rows[0]): t, **{str(c): 0 for c in rows[1:]}}
        r = node_run(compiled, [{"kind": "error", "target": name, "value": spread(total + 1), "answers": {}},
                                {"kind": "error", "target": name, "value": spread(total), "answers": {}}])
        if r and r[0] is not True:
            out.append(Diff("CONSTRAINT_DIFF", "link_sum_not_enforced", f"a total of {total + 1} (document says {total}) is accepted"))
        if r and r[1] is True:
            out.append(Diff("CONSTRAINT_DIFF", "link_sum_rejects_valid", f"the correct total {total} is refused"))
    return out


# ---------------------------------------------------------------- flags
def flag_index(survey: SurveyDefinition, audit: list, checks: list[dict]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for q in survey.questions:
        for f in q.flags or []:
            idx[nq(q.qid)].append(f"flag:{f}")
        for t in (q.validation or {}).get("qa_issues") or []:
            idx[nq(q.qid)].append(f"qa:{t}")
    for e in (survey.metadata.get("qa") or {}).get("entries") or []:
        if e.get("status") == "needs_you":
            idx[nq(e["qid"])].append(f"qa:{e.get('check')}: {e.get('detail')}")
    for f in audit:
        for qid in f.qids:
            idx[nq(qid)].append(f"audit:{f.check}: {f.detail}")
    for c in checks:
        if c["status"] != "pass":
            for qid in c.get("qids") or []:
                idx[nq(qid)].append(f"preflight:{c['id']}: {c['detail']}")
    for pr in survey.validate():
        if m := re.search(r"question (\S+?):", pr):
            idx[nq(m.group(1))].append(f"validate:{pr}")
    return idx


def defect_hit(d: dict, reasons: list[str]) -> str:
    if not reasons:
        return "SILENT"
    words = DEFECT_WORDS.get(d["kind"])
    blob = " ".join(reasons).casefold()
    if words is None:  # "other": any flag on that qid counts
        return "FLAGGED"
    if any(w in blob for w in words):
        return "FLAGGED"
    detail_words = {w for w in re.findall(r"[a-z]{5,}", str(d.get("detail", "")).casefold())}
    if len(detail_words & set(re.findall(r"[a-z]{5,}", blob))) >= 2:
        return "FLAGGED"
    return "FLAGGED_WRONG_REASON"


# ---------------------------------------------------------------- one document
def pick(diffs: list[Diff]) -> str:
    vs = {d["verdict"] for d in diffs if not d["kind"].endswith("_unchecked")}
    return next((s for s in SEVERITY if s in vs), "MATCH")


def run_document(docx: Path, gold: dict, baseline: dict | None = None) -> dict:
    t0 = time.time()
    assert_ai_off()
    blocks = load_docx(str(docx))
    chunks = segment_into_questions(blocks)
    survey = extract_survey_definition(chunks, "eval", blocks=blocks)
    t_extract = time.time() - t0
    compiled = compile_to_surveyjs(survey)
    try:
        checks = preflight(survey, True, frozenset(), set())["checks"]
    except Exception as exc:  # noqa: BLE001 - a crash here must not hide the comparison
        checks = [{"id": "preflight_crashed", "status": "fail", "detail": repr(exc), "qids": []}]
    flags = flag_index(survey, audit_definition(survey), checks)
    links = link_index(compiled)
    gqs = gold["questions"]
    gold_by = {nq(g["qid"]): g for g in gqs}
    matches = match_questions(gqs, survey.questions)
    ALIAS.clear()
    ALIAS.update({nq(q.qid): nq(gqs[i]["qid"]) for i, (q, _) in matches.items()})
    parser_by = {ALIAS.get(nq(q.qid), nq(q.qid)): q for q in survey.questions}
    gold_only: list[dict] = []
    matched_ids = {id(m[0]) for m in matches.values()}
    items = []
    seen: dict[str, int] = defaultdict(int)
    for i, g in enumerate(gqs):
        seen[g["qid"]] += 1
        key = f"{g['qid']}#{seen[g['qid']]}"
        q, how = matches.get(i, (None, "none"))
        el, page = links.get(nq(q.qid), (None, None)) if q else (None, None)
        asked = g.get("asked", True) and g.get("kind", "question") == "question"
        if q is None and not asked:  # invented ids for info screens etc: no counterpart is fine
            gold_only.append({"qid": g["qid"], "kind": g.get("kind"), "text": clip(g.get("text"), 120)})
            continue
        l1: list[Diff] = []
        if q is None:
            if asked and not g.get("struck_out"):
                l1.append(Diff("MISSING", "missing_question", f"{g['type']} question not found by the parser"))
        else:
            fielded = q.type is not QT.INFO and el is not None
            if how == "text" and asked:
                l1.append(Diff("QID_SHIFT", "qid_shift", f"document says {g['qid']}, parser numbered it {q.qid}"))
            if g.get("struck_out") and fielded:
                l1.append(Diff("STRUCK_FIELDED", "struck_fielded", "struck out in the document, fielded by the parser"))
            elif not asked and fielded:
                l1.append(Diff("TYPE_DIFF", "fielded_not_asked", "not asked in the document (info/hidden) but fielded as a question"))
            elif asked and not g.get("struck_out"):
                l1 += _parser_diffs(g, q)
        ld: list[Diff] = []
        gshow, gterm = g.get("show_if"), gold_terminate(g)
        if asked and not g.get("struck_out") and not g.get("show_if_expr_unparseable"):
            try:
                if not (g.get("expr_unparseable") and gshow is None):
                    pshow, lshow = behaviour("show_if", f"{gold['doc_id']}|{key}|show", gshow, q.show_if if q else None,
                                             link_visible(el, page) if el else None, gold_by, parser_by,
                                             (compiled, el["name"]) if el else None)
                    l1 += pshow
                    ld += lshow
                if gterm or (q and q.terminate_if):
                    # screen-outs are not in the link JSON: the server runs evaluate(terminate_if)
                    pterm, _ = behaviour("terminate_if", f"{gold['doc_id']}|{key}|term", gterm,
                                         q.terminate_if if q else None, None, gold_by, parser_by, None)
                    l1 += pterm
            except (KeyError, StopIteration, TypeError, ValueError) as exc:
                ld.append(Diff("LOGIC_DIFF", "logic_unchecked", f"gold condition not evaluable: {exc!r}"))
        l2 = cmp_link(g, el, page, q, links)
        if asked and not g.get("struck_out") and el is not None:
            l2 += probe_diffs(g, el, compiled, gold_by)
        if asked and not g.get("struck_out") and el is not None:
            l2 += ld
        elif asked and q is None and not l2:
            l2 = [Diff("MISSING", "link_missing", "absent")]
        reasons = flags.get(nq(q.qid) if q else nq(g["qid"]), [])
        items.append({
            "key": key, "qid": g["qid"], "match": how, "asked": asked,
            "gold": {"type": g["type"], "text": g.get("text"), "options": g.get("options"),
                     "rows": g.get("rows"), "columns": g.get("columns"), "show_if_text": g.get("show_if_text"),
                     "show_if": gold_str(gshow), "struck_out": g.get("struck_out", False)},
            "parser": None if q is None else {
                "qid": q.qid, "type": q.type.value, "text": q.text, "options": p_opts(q.options),
                "rows": p_opts(q.rows), "cols": p_opts(q.cols), "show_if": expr_str(q.show_if),
                "terminate_if": expr_str(q.terminate_if), "validation": {k: v for k, v in (q.validation or {}).items() if k != "techniques"}},
            "link": link_got(el),
            "layer1": {"verdict": pick(l1), "diffs": l1},
            "layer2": {"verdict": pick(l2), "diffs": l2},
            "flagged": bool(reasons), "flag_reasons": reasons[:6],
        })
    extra = [{"qid": q.qid, "type": q.type.value, "text": q.text, "in_link": nq(q.qid) in links,
              "flagged": bool(flags.get(nq(q.qid)))}
             for q in survey.questions if id(q) not in matched_ids and q.type is not QT.INFO]
    defects = []
    for d in gold.get("defects") or []:
        mi = next((i for i, g in enumerate(gqs) if nq(g["qid"]) == nq(d["qid"])), None)
        pq = matches.get(mi, (None,))[0] if mi is not None else None
        reasons = flags.get(nq(pq.qid) if pq else nq(d["qid"]), [])
        defects.append({**d, "result": defect_hit(d, reasons), "reasons": reasons[:5]})
    res = {"doc_id": gold["doc_id"], "source_file": str(docx), "seconds_extract": round(t_extract, 1),
           "seconds_total": round(time.time() - t0, 1), "parser_questions": len(survey.questions),
           "gold_questions": len(gqs), "items": items, "extra": extra, "defects": defects,
           "gold_only_ids": gold_only,
           "qid_shifted": [{"gold": it["qid"], "parser": it["parser"]["qid"]} for it in items
                           if it["match"] == "text" and it["asked"]]}
    res["scores"] = scores(res)
    res["patterns"] = patterns([res])
    res["compare_baseline"] = diff_baseline(res, baseline) if baseline else None
    return res


def _parser_diffs(g: dict, q: Question) -> list[Diff]:
    out: list[Diff] = []
    want = GOLD_TYPES.get(g["type"], None)
    if want is not None and q.type not in want:
        out.append(Diff("TYPE_DIFF", f"type {g['type']}->{q.type.value}", f"type: ASKED {g['type']} GOT {q.type.value}"))
        return out
    if not same_text(g.get("text"), q.text):
        out.append(Diff("TEXT_DIFF", "text", f"text: ASKED '{clip(g.get('text'), 90)}' GOT '{clip(q.text, 90)}'"))
    if g.get("options") or q.options:
        out += cmp_options(g.get("options") or [], p_opts(q.options), "option", True)
    if g.get("rows") or q.rows:
        out += cmp_options(g.get("rows") or [], p_opts(q.rows), "row", False)
    if g.get("columns") or q.cols:
        out += cmp_options(g.get("columns") or [], p_opts(q.cols), "column", False)
    out += cmp_constraints(g, parser_limits(q), "PARSER")
    rnd = (g.get("randomize") or {}).get("options")
    if rnd is not None and bool(rnd) != bool(q.randomize_options):
        out.append(Diff("CONSTRAINT_DIFF", "constraint_randomize", f"randomize options: ASKED {rnd} GOT {q.randomize_options}"))
    out += cmp_list_pipes(g, q)
    return out


# ---------------------------------------------------------------- scoring, patterns, baseline
def scores(res: dict) -> dict:
    out: dict[str, Any] = {}
    n = len(res["items"]) or 1
    for layer in ("layer1", "layer2"):
        bad = [it for it in res["items"] if it[layer]["verdict"] != "MATCH"]
        silent = [it for it in bad if not it["flagged"]]
        out[layer] = {"items": len(res["items"]), "match_pct": round(100 * (n - len(bad)) / n, 1),
                      "flagged_miss_pct": round(100 * (len(bad) - len(silent)) / n, 1),
                      "silent_wrong_pct": round(100 * len(silent) / n, 1),
                      "silent_wrong": [it["key"] for it in silent],
                      "match_but_flagged": sum(1 for it in res["items"]
                                               if it[layer]["verdict"] == "MATCH" and it["flagged"])}
    d = res["defects"]
    out["defects"] = {"total": len(d),
                      "detected_pct": round(100 * sum(x["result"] == "FLAGGED" for x in d) / (len(d) or 1), 1),
                      "wrong_reason": sum(x["result"] == "FLAGGED_WRONG_REASON" for x in d),
                      "silent": sum(x["result"] == "SILENT" for x in d)}
    out["extra_questions"] = len(res["extra"])
    out["qid_shifted"] = len(res["qid_shifted"])
    out["gold_only_ids"] = len(res["gold_only_ids"])
    return out


def patterns(results: list[dict]) -> list[dict]:
    groups: dict[tuple, list[str]] = defaultdict(list)
    for res in results:
        for it in res["items"]:
            for layer in ("layer1", "layer2"):
                for d in it[layer]["diffs"]:
                    if d["kind"].endswith("_unchecked"):
                        continue
                    groups[(layer, d["verdict"], d["kind"])].append(
                        f"{res['doc_id']}:{it['qid']}{'' if it['flagged'] else '(silent)'}")
        for e in res["extra"]:
            groups[("layer1", "EXTRA", "extra_question")].append(f"{res['doc_id']}:{e['qid']}")
        for dd in res["defects"]:
            if dd["result"] != "FLAGGED":
                groups[("defects", dd["result"], dd["kind"])].append(f"{res['doc_id']}:{dd['qid']}")
    return [{"layer": k[0], "verdict": k[1], "pattern": k[2], "count": len(v), "examples": v[:8]}
            for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


def diff_baseline(res: dict, base: dict) -> dict:
    prev = {it["key"]: it for it in base.get("items", [])}
    reg, fix = [], []
    for it in res["items"]:
        b = prev.get(it["key"])
        for layer in ("layer1", "layer2"):
            if not b:
                continue
            was, now = b[layer]["verdict"] == "MATCH", it[layer]["verdict"] == "MATCH"
            if was and not now:
                reg.append({"key": it["key"], "layer": layer, "now": it[layer]["verdict"],
                            "detail": [d["detail"] for d in it[layer]["diffs"]][:2]})
            elif now and not was:
                fix.append({"key": it["key"], "layer": layer, "was": b[layer]["verdict"]})
    return {"regressions": reg, "fixes": fix}


# ---------------------------------------------------------------- report
def p_cell(it: dict) -> str:
    p = it["parser"]
    if not p:
        return "(absent)"
    return clip(f"{p['type']}: {p['text']}"
                + (f" | opts {opts_str(p['options'])}" if p["options"] else "")
                + (f" | rows {len(p['rows'])} cols {len(p['cols'])}" if p["rows"] or p["cols"] else "")
                + (f" | base {p['show_if']}" if p["show_if"] != "none" else "")
                + (f" | term {p['terminate_if']}" if p["terminate_if"] != "none" else ""), 330)


def l_cell(it: dict) -> str:
    link = it["link"]
    if not link["present"]:
        return "(not shown)"
    return clip(f"{link['element']}: {link['title']}"
                + (f" | choices {opts_str(link['choices'])}" if link["choices"] else "")
                + (f" | visibleIf {link['visibleIf']}" if link["visibleIf"] else ""), 330)


def g_cell(it: dict) -> str:
    g = it["gold"]
    return clip(f"{g['type']}{' STRUCK' if g['struck_out'] else ''}: {g['text']}"
                + (f" | opts {opts_str(g['options'])}" if g["options"] else "")
                + (f" | base {g['show_if_text'] or g['show_if']}" if g["show_if"] != "none" else ""), 330)


def verdict_cell(it: dict) -> str:
    bits = []
    for tag, layer in (("P", "layer1"), ("L", "layer2")):
        v = it[layer]
        bits.append(f"{tag}:{v['verdict']}")
        bits += [clip(d["detail"], 260) for d in v["diffs"]]
    return "<br>".join(bits)


def flag_cell(it: dict) -> str:
    bad = it["layer1"]["verdict"] != "MATCH" or it["layer2"]["verdict"] != "MATCH"
    if it["flagged"]:
        return ("FLAGGED " if bad else "over-flag ") + clip(it["flag_reasons"][0], 90)
    return "SILENT" if bad else "-"


def pattern_md(pats: list[dict]) -> list[str]:
    out = ["| count | layer | verdict | pattern | examples |", "|---|---|---|---|---|"]
    out += [f"| {p['count']} | {p['layer']} | {p['verdict']} | {p['pattern']} | {clip(', '.join(p['examples']), 200)} |"
            for p in pats]
    return out


def score_md(sc: dict) -> list[str]:
    out = ["| layer | items | MATCH % | flagged miss % | SILENTLY WRONG % | match but flagged |", "|---|---|---|---|---|---|"]
    for layer, label in (("layer1", "1 qnre vs parser"), ("layer2", "2 qnre vs link")):
        s = sc[layer]
        out.append(f"| {label} | {s['items']} | {s['match_pct']} | {s['flagged_miss_pct']} | "
                   f"{s['silent_wrong_pct']} | {s['match_but_flagged']} |")
    d = sc["defects"]
    out.append(f"| 3 defects | {d['total']} | {d['detected_pct']} detected | wrong reason {d['wrong_reason']} | "
               f"silent {d['silent']} | - |")
    return out


def report_md(res: dict, rows: bool = True) -> str:
    L = [f"# {res['doc_id']}", "", f"source: {res['source_file']}  ", f"gold questions {res['gold_questions']}, "
         f"parser questions {res['parser_questions']}, extra {len(res['extra'])}, "
         f"{res['seconds_total']}s (extraction {res['seconds_extract']}s)", "", "## Scores", ""] + score_md(res["scores"])
    cb = res.get("compare_baseline")
    if cb:
        L += ["", f"## Regressions ({len(cb['regressions'])})", ""]
        L += [f"- {r['key']} {r['layer']} now {r['now']}: {'; '.join(r['detail'])}" for r in cb["regressions"]] or ["none"]
        L += ["", f"## Fixes ({len(cb['fixes'])})", ""]
        L += [f"- {f['key']} {f['layer']} was {f['was']}" for f in cb["fixes"]] or ["none"]
    if rows:
        L += ["", "## Root-cause candidates (by pattern)", ""] + pattern_md(res["patterns"])
        L += ["", "## Every question", "", "| qid | ASKED (gold) | PARSER GOT | LINK GOT | verdict | flagged? |",
              "|---|---|---|---|---|---|"]
        L += [f"| {it['qid']} | {g_cell(it)} | {p_cell(it)} | {l_cell(it)} | {verdict_cell(it)} | {flag_cell(it)} |"
              for it in res["items"]]
        L += ["", f"## QID_SHIFT ({len(res['qid_shifted'])} questions numbered differently from the document)", ""]
        L += [f"- document {x['gold']} = parser {x['parser']}" for x in res["qid_shifted"][:60]] or ["none"]
        L += ["", "## Gold-only ids (no parser counterpart expected)", ""]
        L += [f"- {x['qid']} ({x['kind']}): {x['text']}" for x in res["gold_only_ids"]] or ["none"]
        L += ["", "## EXTRA (parser questions not in gold)", ""]
        L += [f"- {e['qid']} [{e['type']}] link={e['in_link']} flagged={e['flagged']}: {clip(e['text'], 120)}"
              for e in res["extra"]] or ["none"]
        L += ["", "## Defects (layer 3)", "", "| qid | kind | detail | result | flag reasons |", "|---|---|---|---|---|"]
        L += [f"| {d['qid']} | {d['kind']} | {clip(d.get('detail'), 140)} | {d['result']} | {clip('; '.join(d['reasons']), 140)} |"
              for d in res["defects"]]
    else:
        L += ["", "(holdout: rows and patterns withheld; see results.json only if you must)"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- CLI
def check_out(out: Path) -> Path:
    out = out.resolve()
    if REPO in (out, *out.parents):
        raise SystemExit(f"REFUSING: --out {out} is inside the repo; outputs hold client content")
    return out


def load_base(path: Path | None, doc_id: str) -> dict | None:
    if not path:
        return None
    f = path / doc_id / "results.json" if path.is_dir() else path
    return json.loads(f.read_text()) if f.exists() else None


def find_pairs(dirs: list[Path]) -> list[tuple[Path, Path]]:
    pairs = []
    for d in dirs:
        for f in sorted(d.rglob("*.json")):
            try:
                g = json.loads(f.read_text())
            except ValueError:
                continue
            if not (isinstance(g, dict) and "questions" in g and "doc_id" in g):
                continue
            cands = [f.with_suffix(".docx"), f.parent / f"{g['doc_id']}.docx", Path(g.get("source_file", "/nonexistent"))]
            docx = next((c for c in cands if c.exists()), None)
            if docx:
                pairs.append((docx, f))
            else:
                print(f"SKIP {f}: no .docx found", file=sys.stderr)
    return pairs


def write_doc(out: Path, res: dict, holdout: bool) -> None:
    d = out / res["doc_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    (d / "report.md").write_text(report_md(res, rows=not holdout))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docx", type=Path)
    ap.add_argument("--gold", type=Path)
    ap.add_argument("--batch", type=Path, nargs="+", help="dirs holding gold .json (+ sibling .docx or source_file)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--baseline", type=Path, help="previous results.json, or a previous --out dir in batch mode")
    a = ap.parse_args(argv)
    assert_ai_off()
    out = check_out(a.out)
    pairs = find_pairs(a.batch) if a.batch else [(a.docx, a.gold)] if a.docx and a.gold else []
    if not pairs:
        ap.error("give --docx and --gold, or --batch DIR")
    results: list[dict] = []
    aborted: list[str] = []
    for docx, gpath in pairs:
        gold = json.loads(gpath.read_text())
        try:
            res = run_document(docx, gold, load_base(a.baseline, gold["doc_id"]))
        except BackendUnavailable as exc:
            # AI is off on purpose; when the deterministic parser cannot read a document the
            # extractor's circuit breaker raises instead of returning what it could read.
            # That is a result (the document produced NO definition), not a reason to stop.
            print(f"{gold['doc_id']}: ABORTED, extraction produced no definition ({str(exc)[:90]})")
            aborted.append(gold["doc_id"])
            continue
        write_doc(out, res, holdout="holdout" in gpath.parts)
        results.append(res)
        s = res["scores"]
        print(f"{res['doc_id']}: parser {s['layer1']['match_pct']}% (silent {s['layer1']['silent_wrong_pct']}%) | "
              f"link {s['layer2']['match_pct']}% (silent {s['layer2']['silent_wrong_pct']}%) | "
              f"defects {s['defects']['detected_pct']}% | extra {s['extra_questions']} | {res['seconds_total']}s")
    if len(results) > 1:
        L = ["# Batch summary", ""]
        for r in results:
            L += [f"## {r['doc_id']}", ""] + score_md(r["scores"]) + [""]
        L += ["## Root-cause candidates across documents", ""] + pattern_md(patterns(results))
        (out / "summary.md").write_text("\n".join(L) + "\n")
        (out / "summary.json").write_text(json.dumps({r["doc_id"]: r["scores"] for r in results}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
