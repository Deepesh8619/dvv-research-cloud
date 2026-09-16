#!/usr/bin/env python3
"""Extract one document N times and report what moved between runs.

    python3 scripts/eval_extraction.py path/to/questionnaire.docx --runs 5

The deterministic parser is covered by tests/unit/test_extraction_golden.py,
which asserts an exact result and zero model calls. This measures the other
half: what a real document does when parts of it DO reach a model, which is
where the instability lives. The same client instrument extracted six times gave
95, 96, 97 and 107 questions — so "the audit was clean" is a statement about one
run, not about the document.

Exits 1 when any tracked number differs across runs, so it can gate a model or
prompt change. Costs one extraction per run against whatever backend is
configured; it is not part of the test suite for that reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.survey_definition import QuestionType, SurveyDefinition  # noqa: E402
from src.engines.questionnaire.definition_audit import audit_definition  # noqa: E402
from src.engines.questionnaire.extractor import extract_survey_definition  # noqa: E402
from src.intake.questionnaire_loader import (  # noqa: E402
    load_docx,
    load_pdf,
    segment_into_questions,
)


def measure(survey: SurveyDefinition) -> dict[str, int]:
    grids = [q for q in survey.questions if q.type is QuestionType.GRID_SINGLE]
    return {
        "questions": len(survey.questions),
        "grids": len(grids),
        "grids_with_rows_and_cols": sum(1 for q in grids if q.rows and q.cols),
        "questions_with_a_base": sum(1 for q in survey.questions if q.show_if),
        "options_total": sum(len(q.options or []) for q in survey.questions),
        "hidden_variables": sum(1 for q in survey.questions if q.qid.startswith("HID_")),
        "audit_findings": len(audit_definition(survey)),
        "read_without_a_model": sum(
            1 for q in survey.questions if q.extraction_method != "llm"
        ),
    }


def content(survey: SurveyDefinition) -> dict[str, str]:
    """Each question as canonical JSON, so a difference in WORDING is visible.

    The counted metrics are counts: three questions here still reach a model,
    and a model can reword a question, drop an option's trailing clause or
    re-split a grid row without moving any total. "Stable" has to mean the
    definition is the same, not that it is the same size.
    """
    return {
        q.qid: json.dumps(q.to_dict(), sort_keys=True, ensure_ascii=False)
        for q in survey.questions
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    loader = load_pdf if args.document.suffix.lower() == ".pdf" else load_docx
    blocks = loader(str(args.document))
    chunks = segment_into_questions(blocks)
    print(f"{args.document.name}: {len(blocks)} blocks -> {len(chunks)} chunks")

    runs: list[dict[str, int]] = []
    contents: list[dict[str, str]] = []
    for i in range(args.runs):
        survey = extract_survey_definition(chunks, "eval", blocks=blocks)
        runs.append(measure(survey))
        contents.append(content(survey))
        print(f"  run {i + 1}: {runs[-1]}")

    print(f"\n{'metric':<28}{'min':>6}{'max':>6}{'spread':>8}  values")
    unstable = []
    for key in runs[0]:
        values = [r[key] for r in runs]
        spread = max(values) - min(values)
        if spread:
            unstable.append(key)
        counts = Counter(values)
        print(
            f"{key:<28}{min(values):>6}{max(values):>6}{spread:>8}  "
            f"{dict(sorted(counts.items()))}"
        )

    # Which questions appeared in some runs and not others, and which came back
    # WORDED differently — neither is visible in a table of totals, and the
    # second is the one a count can never catch.
    qid_sets = [frozenset(c) for c in contents]
    everywhere = frozenset.intersection(*qid_sets)
    sometimes = sorted(frozenset.union(*qid_sets) - everywhere)
    if sometimes:
        print(f"\nquestions present in SOME runs only ({len(sometimes)}): {sometimes}")

    reworded = sorted(
        qid for qid in everywhere if len({c[qid] for c in contents}) > 1
    )
    if reworded:
        print(f"\nquestions whose CONTENT differed between runs ({len(reworded)}): {reworded}")
        first = contents[0][reworded[0]]
        other = next(c[reworded[0]] for c in contents if c[reworded[0]] != first)
        print(f"  e.g. {reworded[0]}:\n    run 1: {first[:180]}\n    later: {other[:180]}")

    if unstable or sometimes or reworded:
        moved = ", ".join(unstable) or ("wording" if reworded else "question set")
        print(f"\nUNSTABLE: {moved} moved between runs.")
        print("Same document, same code. A client re-extracting gets a different")
        print("instrument, so any comparison against a baseline is unreliable here.")
        return 1

    note = (
        f"every question byte-identical across {args.runs} runs"
        if args.runs > 1
        else "one run only — run with --runs 3 or more to say anything about stability"
    )
    print(f"\nSTABLE: {note}.")
    if runs:
        share = runs[0]["read_without_a_model"] / max(runs[0]["questions"], 1)
        print(f"Read without a model: {share:.0%} of questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
