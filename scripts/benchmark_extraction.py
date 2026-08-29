"""Accuracy oracle for questionnaire extraction.

The test suite proves the extraction PLUMBING works. Nothing in it proves
extraction is any *good* — so a model swap or a bad learned rule can degrade
real accuracy while 516 tests stay green. This is the missing measurement.

Two modes, because there are two questions worth asking:

  Structural (default, no labels needed)
      Did we find every question the document structurally contains? Do the
      questions we found have the fields they should? Runs against any client
      document immediately, with nothing to prepare.

  Gold (--gold)
      Compare field-by-field against known-correct answers. The gold file is
      built from questions a HUMAN APPROVED in the review UI (--make-gold),
      so labelling is a by-product of work the team already does rather than a
      separate chore.

The comparison that matters most for the correction loop is `--ab`: run the
same document with learned rules OFF and ON, and diff. That is the "Vishal
wrote one sentence and 41 questions improved" number, measured rather than
asserted.

Run:
    python scripts/benchmark_extraction.py DOC.docx
    python scripts/benchmark_extraction.py DOC.docx --ab --profile gbk_spec_table
    python scripts/benchmark_extraction.py DOC.docx --gold gold/gbk.json
    python scripts/benchmark_extraction.py --make-gold STUDY_ID --out gold/gbk.json

Model is whatever the router resolves (see src/ai/router.py); set GROQ_MODEL /
HF_MODEL to benchmark a different one. Extraction costs one LLM call per chunk,
so use --limit while iterating.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engines.questionnaire.extractor import (  # noqa: E402
    extract_survey_definition,
)
from src.engines.questionnaire.profiles import compute_stats, detect_profile  # noqa: E402
from src.intake.questionnaire_loader import (  # noqa: E402
    load_docx,
    load_pdf,
    segment_into_questions,
)

# Types that must carry answer options to be usable; an empty option list on
# these is a real extraction defect, not a stylistic difference.
_CHOICE_TYPES = {"single", "multi"}


def _load(path: Path):
    if path.suffix.lower() == ".docx":
        return load_docx(path)
    if path.suffix.lower() == ".pdf":
        return load_pdf(path)
    raise SystemExit(f"Unsupported document type: {path.suffix} (want .docx or .pdf)")


def _rules_provider(rules: list[str] | None):
    """A rules_for_profile callable that ignores the slug and returns `rules`.

    Lets the A/B run force a specific rule set (including none) rather than
    whatever happens to be in the database.
    """
    if rules is None:
        return None
    return lambda _slug: rules


def _db_rules(profile_slug: str) -> list[str]:
    """Active learned rules for a profile, straight from the app database."""
    from api.db import SessionLocal, active_rules_for_profile

    db = SessionLocal()
    try:
        return active_rules_for_profile(db, profile_slug)
    finally:
        db.close()


# --------------------------------------------------------------------------
# Structural metrics — checkable without any labels
# --------------------------------------------------------------------------


def measure(survey, blocks, elapsed: float) -> dict[str, Any]:
    """Everything we can assert about an extraction without a gold standard."""
    stats = compute_stats(blocks)
    questions = survey.questions

    def _conf(q, field: str) -> float | None:
        c = getattr(q, "extraction_confidence", None) or {}
        v = c.get(field)
        return v if isinstance(v, (int, float)) else None

    confidences = [
        v for q in questions for f in ("text", "type", "options", "routing")
        if (v := _conf(q, f)) is not None
    ]
    choice_qs = [q for q in questions if str(getattr(q.type, "value", q.type)) in _CHOICE_TYPES]

    flags: dict[str, int] = {}
    for q in questions:
        for f in getattr(q, "flags", []) or []:
            flags[f] = flags.get(f, 0) + 1

    n = len(questions) or 1
    return {
        "n_blocks": stats.n_blocks,
        "question_ish_blocks": stats.question_ish,
        "n_extracted": len(questions),
        # Recall proxy: the segmenter's structural count is the closest thing
        # to ground truth available with no labels. Over 100% means the
        # reconciler recovered questions the regex missed (good) OR the model
        # split one question into several (bad) — inspect, don't assume.
        "coverage_pct": round(100 * len(questions) / (stats.question_ish or 1), 1),
        "with_text_pct": round(100 * sum(1 for q in questions if (q.text or "").strip()) / n, 1),
        "choice_questions": len(choice_qs),
        "choice_with_options_pct": (
            round(100 * sum(1 for q in choice_qs if q.options) / len(choice_qs), 1)
            if choice_qs else None
        ),
        # The field the owner specifically called out as being missed.
        "with_terminate_pct": round(
            100 * sum(1 for q in questions if getattr(q, "terminate_if", None)) / n, 1
        ),
        "with_routing_pct": round(
            100 * sum(1 for q in questions if getattr(q, "show_if", None)) / n, 1
        ),
        "mean_confidence": round(statistics.mean(confidences), 3) if confidences else None,
        "low_confidence_pct": (
            round(100 * sum(1 for c in confidences if c < 0.8) / len(confidences), 1)
            if confidences else None
        ),
        "flagged_questions": sum(1 for q in questions if getattr(q, "flags", None)),
        "flags": dict(sorted(flags.items(), key=lambda kv: -kv[1])),
        "elapsed_s": round(elapsed, 1),
    }


# --------------------------------------------------------------------------
# Gold comparison — field-level accuracy against human-approved answers
# --------------------------------------------------------------------------

_GOLD_FIELDS = ("text", "type", "options", "terminate_if", "show_if")


def _norm(q, field: str):
    """Comparable form of one field. Options compare as (code, label) pairs."""
    if field == "options":
        return [
            (o.get("code") if isinstance(o, dict) else o.code,
             str(o.get("label") if isinstance(o, dict) else o.label).strip().lower())
            for o in (q.get("options") if isinstance(q, dict) else q.options) or []
        ]
    v = q.get(field) if isinstance(q, dict) else getattr(q, field, None)
    if field == "type":
        return str(getattr(v, "value", v) or "").lower()
    if field == "text":
        return " ".join(str(v or "").split()).lower()
    if v is None:
        return None
    # Routing expressions: compare serialized shape, not object identity.
    return json.dumps(v if isinstance(v, dict) else v.to_dict(), sort_keys=True)


def score_against_gold(survey, gold: dict[str, dict]) -> dict[str, Any]:
    """Per-field agreement with the approved answers, plus what went missing."""
    got = {q.qid: q for q in survey.questions}
    per_field = {f: {"match": 0, "total": 0} for f in _GOLD_FIELDS}
    mismatches: list[dict] = []

    for qid, gold_q in gold.items():
        actual = got.get(qid)
        if actual is None:
            mismatches.append({"qid": qid, "field": "*", "reason": "not extracted"})
            for f in _GOLD_FIELDS:
                per_field[f]["total"] += 1
            continue
        for f in _GOLD_FIELDS:
            per_field[f]["total"] += 1
            g, a = _norm(gold_q, f), _norm(actual, f)
            if g == a:
                per_field[f]["match"] += 1
            else:
                mismatches.append(
                    {"qid": qid, "field": f, "gold": str(g)[:120], "got": str(a)[:120]}
                )

    accuracy = {
        f: (round(100 * v["match"] / v["total"], 1) if v["total"] else None)
        for f, v in per_field.items()
    }
    total_m = sum(v["match"] for v in per_field.values())
    total_t = sum(v["total"] for v in per_field.values()) or 1
    return {
        "gold_questions": len(gold),
        "missing_from_extraction": sum(1 for m in mismatches if m["field"] == "*"),
        "field_accuracy_pct": accuracy,
        "overall_accuracy_pct": round(100 * total_m / total_t, 1),
        "mismatches": mismatches[:40],
    }


def make_gold(study_id: str) -> dict[str, dict]:
    """Build a gold file from the APPROVED questions of a study.

    Approval in the review UI means a human read that question against the
    source and accepted it — which is exactly the labelling a gold standard
    needs. So the team's existing review work becomes the oracle, instead of a
    separate labelling exercise nobody has time for.
    """
    from api.db import SessionLocal, list_questionnaires_for_study

    db = SessionLocal()
    try:
        rows = list_questionnaires_for_study(db, study_id)
        if not rows or not rows[0].definition_json:
            raise SystemExit(f"No extracted questionnaire for study {study_id}")
        definition = json.loads(rows[0].definition_json)
        approved = set(json.loads(rows[0].approved_qids or "[]"))
    finally:
        db.close()

    if not approved:
        raise SystemExit(
            f"Study {study_id} has no approved questions yet — approve some in the "
            "review UI first, then re-run --make-gold."
        )
    return {
        q["qid"]: {f: q.get(f) for f in _GOLD_FIELDS}
        for q in definition.get("questions", [])
        if q.get("qid") in approved
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _row(label: str, a, b=None) -> str:
    if b is None:
        return f"  {label:<28} {a if a is not None else '—'}"
    delta = ""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        d = round(b - a, 1)
        delta = f"   {'+' if d > 0 else ''}{d}" if d else "    ="
    return f"  {label:<28} {str(a):<12} -> {str(b):<12}{delta}"


def report(m: dict, m2: dict | None = None) -> None:
    print("\nSegmentation")
    print(_row("blocks in document", m["n_blocks"], m2["n_blocks"] if m2 else None))
    print(_row("question-ish blocks", m["question_ish_blocks"],
               m2["question_ish_blocks"] if m2 else None))
    print("\nExtraction")
    for key, label in (
        ("n_extracted", "questions extracted"),
        ("coverage_pct", "coverage %"),
        ("with_text_pct", "with question text %"),
        ("choice_with_options_pct", "choice qs with options %"),
        ("with_terminate_pct", "with terminate %"),
        ("with_routing_pct", "with routing %"),
        ("mean_confidence", "mean confidence"),
        ("low_confidence_pct", "below 0.8 confidence %"),
        ("flagged_questions", "flagged questions"),
        ("elapsed_s", "elapsed seconds"),
    ):
        print(_row(label, m.get(key), m2.get(key) if m2 else None))
    if m.get("flags"):
        print("\nFlags (before)" if m2 else "\nFlags")
        for f, c in list(m["flags"].items())[:8]:
            print(f"  {f:<40} {c}")
    if m2 and m2.get("flags"):
        print("Flags (after)")
        for f, c in list(m2["flags"].items())[:8]:
            print(f"  {f:<40} {c}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("document", nargs="?", help="Client .docx/.pdf to benchmark")
    p.add_argument("--limit", type=int, help="Only extract the first N chunks (cheap iteration)")
    p.add_argument("--gold", help="Gold JSON to score against")
    p.add_argument("--make-gold", metavar="STUDY_ID",
                   help="Write a gold file from a study's APPROVED questions")
    p.add_argument("--ab", action="store_true",
                   help="Run twice — learned rules OFF then ON — and diff")
    p.add_argument("--profile", help="Force the profile slug whose rules --ab loads")
    p.add_argument("--out", help="Write the run record (or gold file) to this path")
    args = p.parse_args()

    if args.make_gold:
        gold = make_gold(args.make_gold)
        out = Path(args.out or f"gold_{args.make_gold}.json")
        out.write_text(json.dumps(gold, indent=2))
        print(f"Wrote {len(gold)} approved questions to {out}")
        return

    if not args.document:
        p.error("a document is required (or use --make-gold)")

    doc = Path(args.document).expanduser()
    if not doc.exists():
        raise SystemExit(f"No such document: {doc}")

    print(f"Loading {doc.name} ...")
    blocks = _load(doc)
    chunks = segment_into_questions(blocks)
    detection = detect_profile(blocks)
    slug = args.profile or detection.profile.slug
    print(f"  {len(blocks)} blocks, {len(chunks)} chunks")
    print(f"  profile: {detection.profile.label} ({detection.confidence:.0%}) [{slug}]")
    if args.limit:
        chunks = chunks[: args.limit]
        print(f"  limited to {len(chunks)} chunks")

    def run(rules: list[str] | None, label: str) -> tuple[Any, dict]:
        print(f"\nExtracting ({label}) — {len(chunks)} LLM calls ...")
        t0 = time.perf_counter()
        survey = extract_survey_definition(
            chunks, study_id="benchmark", blocks=blocks,
            rules_for_profile=_rules_provider(rules),
        )
        return survey, measure(survey, blocks, time.perf_counter() - t0)

    if args.ab:
        rules = _db_rules(slug)
        if not rules:
            raise SystemExit(
                f"No active learned rules for profile '{slug}' — nothing to A/B. "
                "Approve a correction in the review UI first."
            )
        print(f"\n{len(rules)} learned rule(s) on '{slug}':")
        for r in rules:
            print(f"  - {r[:100]}")
        _, before = run([], "rules OFF")
        survey, after = run(rules, "rules ON")
        report(before, after)
        record = {"document": doc.name, "profile": slug, "rules": rules,
                  "before": before, "after": after}
    else:
        survey, m = run(None, "database rules")
        report(m)
        record = {"document": doc.name, "profile": slug, "metrics": m}

    if args.gold:
        gold = json.loads(Path(args.gold).read_text())
        scored = score_against_gold(survey, gold)
        record["gold"] = scored
        print(f"\nAgainst gold ({scored['gold_questions']} approved questions)")
        print(_row("overall accuracy %", scored["overall_accuracy_pct"]))
        for f, v in scored["field_accuracy_pct"].items():
            print(_row(f"  {f} %", v))
        if scored["missing_from_extraction"]:
            print(f"  NOT EXTRACTED AT ALL: {scored['missing_from_extraction']}")
        for mm in scored["mismatches"][:8]:
            if mm["field"] != "*":
                print(f"    {mm['qid']}.{mm['field']}: gold={mm['gold']!r} got={mm['got']!r}")

    if args.out:
        Path(args.out).write_text(json.dumps(record, indent=2))
        print(f"\nRun record -> {args.out}")


if __name__ == "__main__":
    main()
