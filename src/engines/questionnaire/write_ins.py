"""The write-in behind "Other (please specify)".

An answer list that offers "Other (Specify)" or "Prefer to self-describe (...)"
is asking for words as well as a code. Without a place to type them the
respondent picks "Other" and the one thing the question exists to learn is lost.

Three places need to agree on what counts, which is why they all read the
`Option.open_other` flag and this module is the only thing that sets it:

  - the extractor, which marks the option (`mark_write_ins`);
  - the compiler, which adds a text box shown only when it is picked;
  - the data file, which holds the text in a column named by `write_in_name`.

Named like Decipher names it, `<qid>r<code>oe`, so the questionnaire-to-data
linker and `variable_roles` already read it as the text behind that punch.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition

# The wording that states a write-in. Deliberately anchored: a bare "Other" is
# NOT a write-in (it is a closed choice), and a spec-table cell that leaked into
# a label ("Among who: Other >>> | If not All, specify: ...") must not match.
_WRITE_IN = re.compile(
    r"\(\s*(?:please\s+)?specify\s*\)"          # Other (Specify) / (please specify)
    r"|\bself-describe\s*\("                     # Prefer to self-describe (...)
    r"|\[\s*other\s+specify\s*\]"                # [OTHER SPECIFY]
    r"|^\W*\d*\W*(?:other|another)\b[^|>]{0,60}?[,:\-–]\s*(?:please\s+)?(?:specify|describe)\b"
    r"|^\W*please\s+(?:specify|describe)\b",     # Please describe: ____
    re.IGNORECASE,
)
# Cell separators from a spec table: the label is not an answer, it is a row dump.
_TABLE_DEBRIS = re.compile(r">>>|\s\|\s")

#: Types whose OPTIONS are an answer list a respondent picks from.
_PICK_TYPES = frozenset({
    QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN, QuestionType.RANK,
})


def is_write_in(label: str | None) -> bool:
    text = label or ""
    return bool(_WRITE_IN.search(text)) and not _TABLE_DEBRIS.search(text)


def write_in_name(qid: str, code: int) -> str:
    """The data column and survey element holding the typed text."""
    return f"{qid}r{code}oe"


def _candidates(question: Question) -> list[Option]:
    if question.type in _PICK_TYPES:
        return list(question.options or [])
    if question.type in (QuestionType.GRID_SINGLE, QuestionType.GRID_MULTI, QuestionType.NUMERIC):
        return list(question.rows or [])
    return []


def write_ins(question: Question) -> list[Option]:
    """The options (grid rows for a grid) that ask the respondent to type."""
    return [o for o in _candidates(question) if o.open_other]


def mark_write_ins(survey: SurveyDefinition | list[Question]) -> list[tuple[str, int]]:
    """Set `open_other` where the label states a write-in. Returns (qid, code) marked."""
    questions = survey.questions if isinstance(survey, SurveyDefinition) else survey
    marked: list[tuple[str, int]] = []
    for question in questions:
        for option in _candidates(question):
            if not option.open_other and is_write_in(option.label):
                option.open_other = True
                marked.append((question.qid, option.code))
    return marked


_PHRASE = re.compile(
    r"\(\s*(?:please\s+)?specify\s*\)|\[[^\]]*\]|_{2,}"
    r"|[,:–]?\s*(?<![\w-])(?:please\s+)?(?:specify|describe)\b.*$",
    re.IGNORECASE,
)


def write_in_prompt(label: str | None) -> str:
    """What the text box asks: "Please specify", plus the choice when it says more.

    "Other (Specify)" -> "Please specify";
    "Prefer to self-describe (Specify)" -> "Please specify (Prefer to self-describe)".
    """
    base = _PHRASE.sub(" ", label or "")
    base = re.sub(r"^\W*\d+(?=[A-Za-z])", "", base)  # "98Another brand" -> "Another brand"
    base = re.sub(r"\s+", " ", base).strip(" -:,.–")
    if base.lower() in ("", "other", "another"):
        return "Please specify"
    return f"Please specify ({base})"
