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
from dataclasses import dataclass, field

from src.core.models import DataMap, VarType
from src.core.survey_definition import Question, SurveyDefinition
from src.engines.questionnaire.linker import LinkReport, link_questions_to_datamap

logger = logging.getLogger(__name__)

# Provenances whose labels are adapter guesses (raw codes / column headers) —
# the only ones this merge may overwrite.
INFERRED_PROVENANCES = frozenset({"csv_import", "excel_import"})


@dataclass
class LabelMergeResult:
    """What the merge changed, for logging/UI."""

    n_variables_relabelled: int = 0
    n_value_labels_applied: int = 0
    skipped_code_mismatch: list[str] = field(default_factory=list)  # variable names
    unmatched_questions: list[str] = field(default_factory=list)


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
            _apply_question_to_variable(question, var, result)

    logger.info(
        "label merge: %d variables relabelled, %d value labels applied, "
        "%d skipped on code mismatch, %d questions unmatched",
        result.n_variables_relabelled,
        result.n_value_labels_applied,
        len(result.skipped_code_mismatch),
        len(result.unmatched_questions),
    )
    return result
