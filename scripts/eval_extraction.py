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
    qid_sets: list[frozenset[str]] = []
    for i in range(args.runs):
        survey = extract_survey_definition(chunks, "eval", blocks=blocks)
        runs.append(measure(survey))
        qid_sets.append(frozenset(q.qid for q in survey.questions))
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

    # Which questions appeared in some runs and not others — the thing a reader
    # of a summary table cannot see, and the thing that matters most.
    everywhere = frozenset.intersection(*qid_sets)
    anywhere = frozenset.union(*qid_sets)
    sometimes = sorted(anywhere - everywhere)
    if sometimes:
        print(f"\nquestions present in SOME runs only ({len(sometimes)}): {sometimes}")

    if unstable or sometimes:
        print(f"\nUNSTABLE: {', '.join(unstable) or 'question set'} moved between runs.")
        print("Same document, same code. A client re-extracting gets a different")
        print("instrument, so any comparison against a baseline is unreliable here.")
        return 1

    spread_note = "identical across every run" if args.runs > 1 else "one run only"
    print(f"\nSTABLE: {spread_note}.")
    if runs:
        share = runs[0]["read_without_a_model"] / max(runs[0]["questions"], 1)
        print(f"Read without a model: {share:.0%} of questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
