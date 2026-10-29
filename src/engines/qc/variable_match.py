"""Questionnaire variables against data columns, one row each: what met, what broke.

The data check already tests codes, ranges, skips, screen-outs, masks, pipes,
exclusive answers, answer counts and sums — but it reported only what broke,
plus totals. "Did S5 pass?" had no answer short of reading the whole list.
This joins every questionnaire question to its columns and to each rule that
was tested on it: met, broken (with how many respondents and which rows), or
not checked and why; then lists the data columns no question claims.
Deterministic: built from the report, never a model.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType

#: Each finding kind, under the rule it breaks.
_RULE_OF = {
    "undeclared_code": "codes", "option_not_scripted": "codes",
    "punch_not_in_questionnaire": "codes", "coded_in_companion": "codes",
    "type_mismatch": "codes", "single_multi": "single", "out_of_range": "range",
    "terminate_violated": "terminate", "terminate_recorded": "terminate",
    "mask_violated": "mask", "pipe_source_empty": "pipe",
    "exclusive_violated": "exclusive", "too_many_answers": "max_select",
    "sum_not_total": "sum",
}

RULE_LABELS = {
    "codes": "Answer codes", "single": "Single or multi", "range": "Number range",
    "skip": "Skip (who is asked)", "terminate": "Screen-out", "mask": "Mask",
    "pipe": "Piping", "exclusive": "Exclusive answer", "max_select": "Most answers allowed",
    "sum": "Adds up to total",
}


def build_variable_match(survey, links, report, routing, unmatched_questions,
                         datamap, recovered: set[str] | frozenset = frozenset()) -> dict:
    """{summary, questions: [...], data_only: [...]} for the crosscheck page."""
    columns_of = {link.qid: list(link.variable_names) for link in links if link.variable_names}
    findings_of: dict[str, list] = {}
    for f in report.findings:
        findings_of.setdefault(f.qid, []).append(f)
    routing_of = {r.qid: r for r in routing}
    missing = set(unmatched_questions)

    rows = []
    for q in survey.questions:
        if q.type == QuestionType.INFO:
            continue  # a screen of text writes no column
        rules: dict[str, dict] = {}
        for kind in report.ran_by_qid.get(q.qid, []):
            rules[kind] = {"rule": kind, "label": RULE_LABELS[kind], "result": "met"}
        for f in findings_of.get(q.qid, []):
            kind = _RULE_OF.get(f.kind, "codes")
            rules[kind] = {"rule": kind, "label": RULE_LABELS[kind],
                           "result": "broken" if f.severity == "error" else "warning",
                           "n_rows": f.n_rows, "detail": f.detail,
                           "example_rows": f.example_rows[:5]}
        r = routing_of.get(q.qid)
        if r is not None and r.checked:
            broke = r.flagged or r.n_outside_base
            rules["skip"] = {"rule": "skip", "label": RULE_LABELS["skip"],
                             "result": "broken" if broke else "met",
                             "n_rows": r.n_outside_base, "detail": r.detail,
                             "agreement_pct": round(r.agreement_pct, 1)}
        cols = columns_of.get(q.qid, [])
        results = {x["result"] for x in rules.values()}
        # Added from the data map: the data has it, the document never asked it.
        status = ("not_in_questionnaire" if q.qid in recovered else
                  "missing_in_data" if not cols and (q.qid in missing or not rules) else
                  "broken" if "broken" in results else
                  "warning" if "warning" in results else
                  "met" if rules else "not_checked")
        rows.append({"qid": q.qid, "text": (q.text or "")[:140], "type": q.type.value,
                     "columns": cols[:12], "n_columns": len(cols), "status": status,
                     "rules": sorted(rules.values(), key=lambda x: list(RULE_LABELS).index(
                         x["rule"]))})

    # Every data column no question claims, with what it is. A question-shaped
    # one is the column to chase; hidden, system and design columns are meant
    # to be absent from a questionnaire, and are shown so nothing is unseen.
    from src.engines.qc.variable_roles import (
        EXPECTED_IN_QUESTIONNAIRE,
        ROLE_LABELS,
        classify_variable,
    )

    claimed = {c for cs in columns_of.values() for c in cs}
    data_only = []
    for v in datamap.variables:
        if v.name in claimed:
            continue
        role = classify_variable(v.name, v.label or "", v.question_ref)
        data_only.append({"column": v.name, "label": (v.label or "")[:120], "role": role,
                          "role_label": ROLE_LABELS[role],
                          "to_chase": role in EXPECTED_IN_QUESTIONNAIRE})
    data_only.sort(key=lambda d: (not d["to_chase"], d["role"], d["column"]))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "summary": {**counts, "questions": len(rows), "data_only": len(data_only),
                    "data_only_to_chase": sum(1 for d in data_only if d["to_chase"]),
                    "rules_met": sum(1 for r in rows for x in r["rules"] if x["result"] == "met"),
                    "rules_broken": sum(1 for r in rows for x in r["rules"]
                                        if x["result"] == "broken")},
        "questions": rows,
        "data_only": data_only,
    }
