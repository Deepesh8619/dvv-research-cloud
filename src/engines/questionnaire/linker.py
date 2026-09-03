"""Link designed questions to collected variables, and cross-check routing.

Two deterministic passes that bridge the two halves of the model:

    1. `link_questions_to_datamap` — join each `Question` (from a designed /
       extracted `SurveyDefinition`) to the `Variable`(s) it produced in the
       collected `DataMap`. Handles the common one-question-to-many-variables
       case where a multi-select explodes into one dummy column per option
       (Decipher-style `Q5r1`/`Q5r2`/... or `Q5_1`/`Q5a`).

    2. `cross_validate_routing` — for questions that both carry a `show_if`
       AND resolved to real columns, check the routing against the actual
       respondent data: rows where a variable was answered but the routing
       says the question should NOT have been shown are a real disagreement
       between the authored instrument and the fielded data.

No LLM is involved. The plan mentions an "LLM tiebreak" tier for questions
that survive all three deterministic tiers unmatched; that is deliberately
NOT built here (see module note below) — a human resolves the residue via a
future review UI. This module stays pure/deterministic and fully testable.

DuckDB usage mirrors `src/engines/analysis/quality.py`: an in-memory
connection with a bounded `memory_limit`/`threads`, reading a `data.parquet`
file via `read_parquet`, driven by `SELECT`s only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from rapidfuzz import fuzz

from src.core.models import DataMap
from src.core.survey_definition import (
    Question,
    QuestionVariableLink,
    SurveyDefinition,
    evaluate,
)

# --- Tunable constants (documented design decisions) ------------------------

# rapidfuzz.fuzz.token_sort_ratio is 0-100. At/above this we accept a label
# fuzzy match. 82 is a defensible middle ground: high enough that unrelated
# wording ("What is your age?" vs "Which brands are you aware of?") stays well
# below it, low enough that real paraphrase/punctuation drift between an
# authored question text and a terse datamap label still clears it. Tune with
# real client data; kept as a module constant so callers/tests share one value.
LABEL_MATCH_THRESHOLD = 82.0

# Below this fraction of "answered rows that the routing would actually show",
# a routing<->data cross-check is flagged for human review. Real fielded data
# is messy (late routing edits, data-processing recodes), so we report a
# percentage and flag rather than hard-fail. <90% agreement is the starting
# flag threshold.
ROUTING_AGREEMENT_FLAG_THRESHOLD = 90.0

# Suffix-stripping regex for the multi-punch / sub-variable case.
#
# Decipher-style exports explode ONE multi-select question into one dummy
# column per option, appending a per-option suffix to the question id:
#
#     Q5   ->  Q5r1, Q5r2, Q5r3        ("r" + row index)
#     Q5   ->  Q5_1, Q5_2, Q5_3        ("_" + index)
#     Q5   ->  Q5a,  Q5b,  Q5c         (single trailing letter)
#     Q5   ->  Q5c1, Q5c2              ("c" + column index, grids)
#
# So to test whether a variable BELONGS TO a question, we strip a trailing
# sub-variable suffix from the variable name and compare the stem to the qid.
# The suffix we strip is, at the end of the string:
#
#     - a REQUIRED separator from {r, c, _} then digits   (Q5r1, Q5c2, Q5_1)
#   OR a single trailing letter that DIRECTLY FOLLOWS A DIGIT  (Q5a, Q5b)
#
# The separator is REQUIRED for the digit form on purpose: a bare trailing-digit
# strip would wreck qids that legitimately end in a digit (Q5 -> "Q", Q10 ->
# "Q1"). Since the enumerated sub-variables always carry a separator
# (r/c/underscore) before their index, requiring it keeps `Q5` intact while
# still collapsing `Q5r1`.
#
# The single-letter form (Q5a, Q5b) requires the letter to sit right after a
# DIGIT — i.e. `<digit>a$`, and we strip only the letter. This is what keeps
# real words / multi-letter recodes intact: `Q5net` ends in "et" (letter after
# letter) so nothing strips, `gender` ends in "er" so nothing strips, but
# `Q5a` (letter after the option-bearing digit) collapses to `Q5`. It is a
# genuine ambiguity — `S1` is a real screener id, not `S`+option-1 — but the
# digit-form's required separator means `S1` never matches this branch, and the
# whole cascade only reaches here AFTER exact/normalized name matching failed.
#
# This is intentionally conservative — it collapses the well-known enumerated
# patterns and little else.
_SUFFIX_RE = re.compile(
    r"""
    (?:                    # a sub-variable suffix is EITHER
        [RC_]\d+           #   required r/c/_ separator then digits  (Q5r1, Q5_1)
      | (?<=\d)[A-Z]       #   OR one trailing letter right after a digit (Q5a)
    )$
    """,
    re.VERBOSE,
)


@dataclass
class LinkReport:
    """Outcome of linking one survey's questions to one datamap.

    `links` holds one `QuestionVariableLink` per confidently-matched question
    (a link may name several variables for a multi/grid). `unmatched_questions`
    are qids that found no confident match; `unmatched_variables` are datamap
    variable names that no question claimed.
    """

    links: list[QuestionVariableLink] = field(default_factory=list)
    unmatched_questions: list[str] = field(default_factory=list)
    unmatched_variables: list[str] = field(default_factory=list)


def _normalize(name: str) -> str:
    """Case/whitespace normalization only — NO suffix stripping.

    Used to detect a match that needed nothing more than uppercasing/trimming,
    which we still report as an ``exact`` method (the identity is unchanged,
    just presentation).
    """
    return name.strip().upper()


def _stem(name: str) -> str:
    """The question-stem of a (possibly sub-variabled) variable name.

    Strips one trailing sub-variable suffix (see `_SUFFIX_RE`) after
    case/whitespace normalization. ``Q5r1`` -> ``Q5``; ``Q5`` -> ``Q5``;
    ``Q5NET`` -> ``Q5NET`` (multi-letter tail preserved).
    """
    norm = _normalize(name)
    return _SUFFIX_RE.sub("", norm)


def _match_by_name(
    question: Question, remaining: dict[str, str]
) -> tuple[list[str], str, float] | None:
    """Try tiers 1-2 (exact, then normalized/suffix) for one question.

    ``remaining`` maps normalized-uppercase variable name -> original name for
    every still-unclaimed variable. Returns
    ``(matched_original_names, method, confidence)`` or None.

    A multi-select explodes to many columns, so name matching returns a LIST:
    every remaining variable whose stem equals the (normalized) qid is claimed
    by this one question.
    """
    qid = question.qid
    qnorm = _normalize(qid)

    # Tier 1: exact, case-sensitive identity qid == variable name.
    for original in remaining.values():
        if original == qid:
            return [original], "exact", 1.0

    # Tier 2a: case/whitespace-only match (still "exact" — same identity).
    exact_norm = [orig for norm, orig in remaining.items() if norm == qnorm]
    if exact_norm:
        return exact_norm, "exact", 1.0

    # Tier 2b: suffix-stripped stem match — the multi-punch / grid case.
    # One qid claims EVERY remaining variable that reduces to its stem. This is
    # "fuzzy" because the variable name is not identical to the qid (a suffix
    # had to be stripped to see the relationship).
    stem_hits = [orig for norm, orig in remaining.items() if _stem(orig) == qnorm]
    if stem_hits:
        return stem_hits, "fuzzy", 1.0

    return None


def _match_by_label(
    question: Question, datamap: DataMap, remaining: dict[str, str]
) -> tuple[list[str], str, float] | None:
    """Tier 3: fuzzy-match the question text against remaining variable labels.

    Compares `question.text` to each remaining variable's `label` with
    `rapidfuzz.fuzz.token_sort_ratio` (word-order-insensitive), picks the best,
    and accepts it only if it clears `LABEL_MATCH_THRESHOLD`. Confidence is the
    score normalized to 0-1. A blank question text or blank label can't match.
    """
    text = (question.text or "").strip()
    if not text:
        return None

    best_name: str | None = None
    best_score = -1.0
    for original in remaining.values():
        label = (datamap[original].label or "").strip()
        if not label:
            continue
        score = fuzz.token_sort_ratio(text, label)
        if score > best_score:
            best_score = score
            best_name = original

    if best_name is not None and best_score >= LABEL_MATCH_THRESHOLD:
        return [best_name], "fuzzy", round(best_score / 100.0, 4)
    return None


def link_questions_to_datamap(
    survey: SurveyDefinition, datamap: DataMap
) -> LinkReport:
    """Link every question to the datamap variable(s) it produced.

    Runs the deterministic matching cascade per question, first confident
    match wins, and never lets one question claim a variable another already
    took:

        1. exact qid == variable name (case-sensitive)          -> exact,  1.0
        2. normalized name / suffix-stripped stem match         -> exact/fuzzy
        3. fuzzy label match above `LABEL_MATCH_THRESHOLD`       -> fuzzy

    Questions are processed in `question_order()` so earlier (screener/demo)
    questions claim their columns before later ones, making linking stable and
    order-deterministic. A question that clears no tier lands in
    `unmatched_questions`; variables no question claimed land in
    `unmatched_variables`.
    """
    report = LinkReport()

    # normalized-name -> original-name, for every unclaimed variable.
    remaining: dict[str, str] = {_normalize(v.name): v.name for v in datamap.variables}

    for qid in survey.question_order():
        if qid not in survey:
            continue
        question = survey[qid]

        match = _match_by_name(question, remaining) or _match_by_label(
            question, datamap, remaining
        )
        if match is None:
            report.unmatched_questions.append(qid)
            continue

        names, method, confidence = match
        report.links.append(
            QuestionVariableLink(
                qid=qid,
                variable_names=list(names),
                match_method=method,  # type: ignore[arg-type]
                confidence=confidence,
            )
        )
        # Remove claimed variables so nothing is double-linked.
        for name in names:
            remaining.pop(_normalize(name), None)

    report.unmatched_variables = sorted(remaining.values())
    return report


# --- Routing-vs-data cross-validator ----------------------------------------


@dataclass
class RoutingValidation:
    """One question's routing<->data agreement check.

    `agreement_pct` is the fraction (0-100) of rows that ANSWERED this question
    for which the `show_if` would actually have shown it. `flagged` is True when
    that falls below `ROUTING_AGREEMENT_FLAG_THRESHOLD`. `detail` explains it in
    human terms (counts, what disagreed).
    """

    qid: str
    agreement_pct: float
    flagged: bool
    detail: str
    #: False when the base could not be evaluated at all. `agreement_pct` is
    #: meaningless then, and a reader must not mistake it for a score.
    checked: bool = True


def _links_by_qid(
    links: list[QuestionVariableLink],
) -> dict[str, QuestionVariableLink]:
    return {link.qid: link for link in links}


def _row_value(row: dict, variable_names: list[str]) -> object | None:
    """The answer a row gave to a question, read from its linked column(s).

    For a single-column question this is the scalar value. For a multi/grid
    (several columns) it's the LIST of columns that are non-missing AND
    non-zero — i.e. the set of "selected" dummies — so an ``in``/``selected``
    routing op over the parent qid behaves sensibly. Returns None if the
    question was not answered at all (every linked column missing).
    """
    present = [(name, row.get(name)) for name in variable_names]
    non_missing = [(name, val) for name, val in present if val is not None]
    if not non_missing:
        return None

    if len(variable_names) == 1:
        return non_missing[0][1]

    # Multi/grid: treat a dummy as "selected" when it is truthy (1, not 0).
    selected: list[object] = []
    for idx, (_name, val) in enumerate(non_missing, start=1):
        try:
            chosen = float(val) != 0.0
        except (TypeError, ValueError):
            chosen = bool(val)
        if chosen:
            selected.append(idx)
    return selected


def _answered(row: dict, variable_names: list[str]) -> bool:
    """True when at least one linked column for this question is non-missing."""
    return any(row.get(name) is not None for name in variable_names)


def _read_rows(parquet_path: Path, columns: list[str]) -> list[dict]:
    """Read the needed columns from parquet as a list of row dicts via DuckDB.

    Only the columns that actually exist in the file are selected; a linked
    variable absent from the data simply reads back as missing everywhere,
    which the caller treats as "not answered".
    """
    con = duckdb.connect()
    try:
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        con.execute(
            f"CREATE VIEW data AS SELECT * FROM read_parquet('{parquet_path}')"
        )
        schema = con.execute("DESCRIBE data").fetchall()
        available = {r[0] for r in schema}
        wanted = [c for c in columns if c in available]
        if not wanted:
            return []
        col_sql = ", ".join(f'"{c}"' for c in wanted)
        cur = con.execute(f"SELECT {col_sql} FROM data")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, vals, strict=True)) for vals in cur.fetchall()]
    finally:
        con.close()


def cross_validate_routing(
    survey: SurveyDefinition,
    links: list[QuestionVariableLink],
    parquet_path: Path,
) -> list[RoutingValidation]:
    """Check each conditionally-shown question's routing against real data.

    For every `Question` that has BOTH a `show_if` AND a resolved link, we ask:
    of the respondents who actually answered this question, for how many would
    the `show_if` have shown it? Rows that answered but that the routing says
    should have been SKIPPED are the disagreement (the plan's "non-missing rows
    should be a subset of rows satisfying show_if" check).

    Per row, an `answers` dict is built by reading each qid the `show_if`
    references from ITS linked column(s), so `evaluate()` can decide the routing
    exactly as the fielded survey would have. Questions whose `show_if` depends
    on a qid we couldn't link are skipped (we cannot honestly evaluate them) and
    reported as un-checkable rather than silently passed.

    Returns one `RoutingValidation` per checked question. If the parquet file
    doesn't exist (a purely-extracted, not-yet-fielded questionnaire), returns
    an empty list rather than crashing.
    """
    if not parquet_path.exists():
        return []

    link_map = _links_by_qid(links)

    # Which questions can we actually check? Need a show_if, a link for this
    # question, and a link for every qid the show_if references.
    checkable: list[Question] = []
    unchecked: list[RoutingValidation] = []
    columns_needed: set[str] = set()
    for qid in survey.question_order():
        if qid not in survey:
            continue
        q = survey[qid]
        if q.show_if is None:
            continue
        refs = q.show_if.referenced_qids()
        missing = [ref for ref in refs if ref not in link_map]
        if qid not in link_map or missing:
            # Not checkable — and saying nothing about it would let a base
            # nobody could verify pass as a base that held.
            unchecked.append(
                RoutingValidation(
                    qid=qid,
                    agreement_pct=0.0,
                    flagged=False,
                    detail=(
                        f"not checked: {qid} is not linked to a column"
                        if qid not in link_map
                        else "not checked: its base tests "
                        + ", ".join(missing)
                        + ", which no column could be found for"
                    ),
                    checked=False,
                )
            )
            continue
        checkable.append(q)
        columns_needed.update(link_map[qid].variable_names)
        for ref in refs:
            columns_needed.update(link_map[ref].variable_names)

    if not checkable:
        return unchecked

    rows = _read_rows(parquet_path, sorted(columns_needed))
    if not rows:
        return unchecked

    results: list[RoutingValidation] = list(unchecked)
    for q in checkable:
        link = link_map[q.qid]
        refs = sorted(q.show_if.referenced_qids())

        answered_rows = 0
        agree = 0
        for row in rows:
            if not _answered(row, link.variable_names):
                continue
            answered_rows += 1
            answers = {
                ref: _row_value(row, link_map[ref].variable_names) for ref in refs
            }
            if evaluate(q.show_if, answers):
                agree += 1

        if answered_rows == 0:
            # Nobody answered it — no evidence either way. Report as full
            # agreement (vacuously true) and don't flag; there is nothing to
            # contradict the routing.
            results.append(
                RoutingValidation(
                    qid=q.qid,
                    agreement_pct=100.0,
                    flagged=False,
                    detail="no respondents answered this question; nothing to check",
                )
            )
            continue

        pct = round(100.0 * agree / answered_rows, 2)
        disagree = answered_rows - agree
        flagged = pct < ROUTING_AGREEMENT_FLAG_THRESHOLD
        detail = (
            f"{agree}/{answered_rows} answered rows "
            f"({pct}%) satisfy show_if; {disagree} answered despite routing "
            "saying they should have been skipped"
        )
        results.append(
            RoutingValidation(
                qid=q.qid, agreement_pct=pct, flagged=flagged, detail=detail
            )
        )

    return results
