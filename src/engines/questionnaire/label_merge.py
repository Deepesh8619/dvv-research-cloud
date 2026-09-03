"""Merge approved-questionnaire labels onto inferred datamap variables.

THE fix for the platform's oldest known gap (§3.2 of the project brain): a
CSV/Excel datafeed export carries no embedded metadata, so the tabular adapter
infers types and uses raw codes ("1", "2", "98") as value labels. The approved
questionnaire knows the real wording — question text and option labels with
their codes. This module joins the two through the deterministic linker
(link_questions_to_datamap) and rewrites the datamap so every downstream
consumer (tables, decks, navigator context) shows human language.

Deliberately conservative:
- Only variables whose labels were INFERRED are touched (provenance
  csv_import / excel_import). SPSS-native and datamap-sheet labels are already
  real and are never overwritten.
- Option labels apply only where the variable's code set is consistent with
  the question's options (codes present in the data must exist in the
  question, not the other way round — surveys legitimately have unchosen
  options). A question whose codes disagree with the data is skipped, not
  forced.
- Purely deterministic — the LLM extracted the questionnaire once (already
  human-approved by this point); no model call happens here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.models import DataMap, VarType
from src.core.survey_definition import Question, SurveyDefinition
from src.engines.questionnaire.linker import LinkReport, link_questions_to_datamap

logger = logging.getLogger(__name__)

# Provenances whose labels are adapter guesses (raw codes / column headers) —
# the only ones this merge may overwrite.
INFERRED_PROVENANCES = frozenset({"csv_import", "excel_import"})


# Option labels are compared on their words, not their punctuation: a data map
# and a questionnaire routinely disagree about a trailing period, a double
# space, or capitalisation while naming the same answer.
_LABEL_WS_RE = re.compile(r"\s+")
# A bracketed insert inside an option label is an instruction to the scripter,
# not words the respondent read: the questionnaire writes
#   "I [IF PPMARIT=1, INSERT: (and/or my spouse)] own [our/my] home"
# and the data map carries some resolved form of the same answer. Stripping
# brackets from BOTH sides is what lets the two recognise each other; it is
# symmetric, so it can only ever join labels that agree on their real words.
_LABEL_INSERT_RE = re.compile(r"\[[^\]]*\]")


def _norm_label(label: str | None) -> str:
    plain = _LABEL_INSERT_RE.sub(" ", label or "")
    return _LABEL_WS_RE.sub(" ", plain.strip()).casefold()


# Below this, a "match" is more likely to be a coincidence (a lone "Yes") than
# evidence that these two lists describe the same answer set.
MIN_LABEL_MATCHES_TO_ALIGN = 2


@dataclass
class LabelMergeResult:
    """What the merge changed, for logging/UI."""

    n_variables_relabelled: int = 0
    n_value_labels_applied: int = 0
    skipped_code_mismatch: list[str] = field(default_factory=list)  # variable names
    unmatched_questions: list[str] = field(default_factory=list)
    # variable name -> {code in the data: code in the questionnaire}
    code_remaps: dict[str, dict[int, int]] = field(default_factory=dict)
    n_codes_aligned: int = 0


def _align_variable_codes(question: Question, var, result: LabelMergeResult) -> None:
    """Renumber an inferred variable's codes to the questionnaire's, by label.

    A CSV/Excel export that ships answer TEXT ("Employed now") rather than
    codes has no codes at all until we invent them, and `tabular.py` invents
    them alphabetically. The questionnaire's codes are the real ones — they are
    what the screen-outs, routing and tab plan are written against — so the two
    describe the same answers under different numbers, and every code-level
    check silently compares apples to oranges. (Measured: 18 of 19 routing
    checks read 0% agreement on the Federal Reserve SHED 2014 file for this
    reason alone. Qualtrics exports choice text by default, so this is the
    ordinary case, not an exotic one.)

    Only ever applied to codes WE invented (`INFERRED_PROVENANCES`); SPSS and
    data-map-sheet codes are the script's own and are never renumbered. The
    label travels with the value throughout, so nothing is reinterpreted —
    only the arbitrary integer identity changes.
    """
    if not var.values or not question.options:
        return

    by_label: dict[str, int] = {}
    for option in question.options:
        key = _norm_label(option.label)
        if not key or key in by_label:
            return  # blank or duplicate option labels — no unambiguous join
        by_label[key] = option.code

    matched: dict[int, int] = {}
    claimed: set[int] = set()
    for code, label in var.values.items():
        target = by_label.get(_norm_label(label))
        if target is None:
            continue
        if target in claimed:
            return  # two data values point at one option — refuse to guess
        claimed.add(target)
        matched[code] = target
    if len(matched) < MIN_LABEL_MATCHES_TO_ALIGN:
        return

    # Values the questionnaire does not list — "Refused", "Don't know" — are
    # real categories, so they keep their own code unless a matched value has
    # taken it, in which case they move to the first free one above everything
    # in play. Their labels are untouched, so nothing about them is reinterpreted.
    remap = dict(matched)
    used = set(matched.values())
    next_free = max(used | set(var.values)) + 1
    for code in sorted(var.values):
        if code in remap:
            continue
        if code not in used:
            remap[code] = code
        else:
            remap[code] = next_free
            next_free += 1
        used.add(remap[code])

    if all(old == new for old, new in remap.items()):
        return  # already aligned — stay idempotent, don't churn the data file

    var.values = {remap[code]: label for code, label in var.values.items()}
    result.code_remaps[var.name] = remap
    result.n_codes_aligned += len(matched)


def _apply_question_to_variable(question: Question, var, result: LabelMergeResult) -> None:
    """Write one question's wording onto one inferred variable (in place)."""
    changed = False

    if question.text and (not var.label or var.label == var.name):
        # The adapter used the column header as the label; the authored
        # question text is strictly more informative.
        var.label = question.text
        changed = True
    if question.text and not var.question_text:
        var.question_text = question.text
        changed = True

    if var.type == VarType.SINGLE and question.options and var.values:
        option_labels = {opt.code: opt.label for opt in question.options}
        data_codes = set(var.values)
        if data_codes <= set(option_labels):
            var.values = {code: option_labels[code] for code in var.values}
            result.n_value_labels_applied += len(var.values)
            changed = True
        else:
            # Codes in the data the question doesn't know — recode drift or a
            # wrong link. Don't guess; leave raw codes and report it.
            result.skipped_code_mismatch.append(var.name)

    if changed:
        result.n_variables_relabelled += 1


def align_codes_to_questionnaire(
    survey: SurveyDefinition,
    datamap: DataMap,
    links: LinkReport | None = None,
) -> LabelMergeResult:
    """Renumber inferred codes to the questionnaire's, by matching option labels.

    The alignment half of the merge on its own, for callers that must not have
    labels rewritten as a side effect — the cross-check reports on the data map
    it was given, so quietly relabelling it mid-check would change what the
    reviewer is being shown. Mutates `datamap`; the caller applies
    `result.code_remaps` to the data file with `remap_data_codes`.
    """
    if links is None:
        links = link_questions_to_datamap(survey, datamap)
    result = LabelMergeResult(unmatched_questions=list(links.unmatched_questions))
    for link in links.links:
        if link.qid not in survey:
            continue
        question = survey[link.qid]
        for name in link.variable_names:
            if name not in datamap:
                continue
            var = datamap[name]
            if var.provenance not in INFERRED_PROVENANCES:
                continue  # SPSS / data map sheet codes are the script's own
            _align_variable_codes(question, var, result)
    if result.code_remaps:
        logger.info(
            "code alignment: %d values renumbered across %d variables",
            result.n_codes_aligned,
            len(result.code_remaps),
        )
    return result


def merge_labels_into_datamap(
    survey: SurveyDefinition,
    datamap: DataMap,
    links: LinkReport | None = None,
) -> LabelMergeResult:
    """Merge the survey's real labels onto the datamap's inferred variables.

    Mutates `datamap` in place and returns what changed. `links` may be passed
    when the caller already ran the linker (the navigator caches one); computed
    here otherwise.
    """
    if links is None:
        links = link_questions_to_datamap(survey, datamap)

    result = LabelMergeResult(unmatched_questions=list(links.unmatched_questions))

    for link in links.links:
        if link.qid not in survey:
            continue
        question = survey[link.qid]
        for name in link.variable_names:
            if name not in datamap:
                continue
            var = datamap[name]
            if var.provenance not in INFERRED_PROVENANCES:
                continue  # real labels (SPSS / datamap sheet) are never touched
            # Codes first: `_apply_question_to_variable` only writes option
            # labels when the data's codes are ones the question knows, so
            # aligning first is what lets it do its job on a label-encoded file.
            _align_variable_codes(question, var, result)
            _apply_question_to_variable(question, var, result)

    logger.info(
        "label merge: %d variables relabelled, %d value labels applied, "
        "%d codes aligned across %d variables, %d skipped on code mismatch, "
        "%d questions unmatched",
        result.n_variables_relabelled,
        result.n_value_labels_applied,
        result.n_codes_aligned,
        len(result.code_remaps),
        len(result.skipped_code_mismatch),
        len(result.unmatched_questions),
    )
    return result


def remap_data_codes(parquet_path: str | Path, remaps: dict[str, dict[int, int]]) -> int:
    """Apply `merge_labels_into_datamap`'s code remaps to the data file.

    Renumbering the data map alone would leave the data disagreeing with it, so
    both move together or neither does. Written through a temporary file and
    renamed, so an interrupted run leaves the original intact rather than a
    half-written parquet. Returns the number of columns rewritten.
    """
    import polars as pl

    parquet_path = Path(parquet_path)
    if not remaps or not parquet_path.exists():
        return 0

    frame = pl.read_parquet(parquet_path)
    present = [name for name in remaps if name in frame.columns]
    if not present:
        return 0
    # `replace` (not `replace_strict`) on purpose: a value outside the data
    # map's declared set stays as it is rather than becoming null. A stray code
    # is a finding for the cross-check to report, not data for us to delete.
    frame = frame.with_columns(
        [pl.col(name).replace(remaps[name]) for name in present]
    )
    tmp = parquet_path.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp)
    tmp.replace(parquet_path)
    logger.info("remapped codes in %d columns of %s", len(present), parquet_path.name)
    return len(present)
