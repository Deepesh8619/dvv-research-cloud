"""Read the Study Overview a questionnaire opens with.

Every GBK document starts with one, and it is the most machine-readable page in
the file: the sample size, the length of interview, the markets, the screening
requirements as plain bullets, and the quotas as a table with a minimum and a
maximum per group.

    QUOTAS
    Quota Groups     | Definition        | Target N | Min N | Max N
    Total N          |                   | 750      |       |
    Micro            | 2-9 Employees     |          | 75    | 125
    Small            | 10-99 Employees   |          | 100   | 250

None of it was read. The chunk carries no question label, so it was set aside
and a scripter retyped the quotas into the platform by hand — the one table in
the document where a typo costs money, because over-filling a cell means paying
for sample the client will not accept.

Nothing here is applied on its own. Quotas are proposed for a human to confirm,
because a quota cell needs a CONDITION against a real question ("2-9 Employees"
is a definition, not an expression) and guessing that mapping would silently
field the wrong study. The screening requirements are cross-checked against what
the screener actually enforces, which is the useful question a document can
answer about itself: it states who should qualify, so we can ask whether any
question in the instrument makes that true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.core.survey_definition import Expression, SurveyDefinition

_HEADINGS = {
    "RESEARCH BACKGROUND",
    "RESEARCH OBJECTIVES",
    "RESEARCH METHODOLOGY",
    "SCREENING REQUIREMENTS",
    "QUOTAS",
    "SURVEY OUTLINE",
    "GENERAL PROGRAMMING NOTES",
    "STUDY OVERVIEW",
}
_KEY_VALUE = re.compile(
    r"^\s*(Markets?|Sample\s*Size|LOI|Length\s*of\s*Interview)\s*:\s*(?P<value>.+?)\s*$", re.I
)
_FIRST_NUMBER = re.compile(r"(\d[\d,]*)")
_QUOTA_HEADER = re.compile(r"quota\s*group|\bdefinition\b", re.I)
# "Max n=100 brick-and-mortar retail only" caps a cell; it never disqualifies
# anybody, so it is not something a screen-out should be expected to enforce.
_QUOTA_REQUIREMENT = re.compile(r"\b(?:max|min)\s*n\s*=|\bquota\b", re.I)


@dataclass
class QuotaTarget:
    """One row of the quota table, as written."""

    group: str
    definition: str
    target_n: int | None = None
    min_n: int | None = None
    max_n: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "definition": self.definition,
            "target_n": self.target_n,
            "min_n": self.min_n,
            "max_n": self.max_n,
        }


@dataclass
class StudyOverview:
    markets: str | None = None
    sample_size: int | None = None
    loi_minutes: int | None = None
    screening_requirements: list[str] = field(default_factory=list)
    quotas: list[QuotaTarget] = field(default_factory=list)
    programming_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "markets": self.markets,
            "sample_size": self.sample_size,
            "loi_minutes": self.loi_minutes,
            "screening_requirements": list(self.screening_requirements),
            "quotas": [q.to_dict() for q in self.quotas],
            "programming_notes": list(self.programming_notes),
        }

    def is_empty(self) -> bool:
        return not any(
            (self.markets, self.sample_size, self.loi_minutes,
             self.screening_requirements, self.quotas, self.programming_notes)
        )


def _number(text: str) -> int | None:
    match = _FIRST_NUMBER.search(text or "")
    return int(match.group(1).replace(",", "")) if match else None


def _heading(text: str) -> str | None:
    stripped = (text or "").strip().rstrip(":").upper()
    return stripped if stripped in _HEADINGS else None


# A document HAS an overview or it does not. GBK's scripter documents open with
# one; the prose questionnaires that come straight from a client typically start
# at the first question. Absence is normal and must cost nothing: no context, no
# findings, no flag. Reporting "no study overview" as a defect would put a
# permanent red mark on every document of the second kind.
_OVERVIEW_SIGNALS = ("STUDY OVERVIEW", "SCREENING REQUIREMENTS", "QUOTAS",
                     "RESEARCH OBJECTIVES", "RESEARCH METHODOLOGY")


def looks_like_overview(blocks) -> bool:
    """True when these blocks open a document with a study overview."""
    text = " ".join((getattr(b, "text", "") or "") for b in blocks).upper()
    return sum(signal in text for signal in _OVERVIEW_SIGNALS) >= 2


def parse_overview(blocks) -> StudyOverview:
    """Read the overview from the blocks of the document's opening chunk.

    Returns an empty overview when the document does not have one, which is not
    an error and not worth a flag.
    """
    if not looks_like_overview(blocks):
        return StudyOverview()
    overview = StudyOverview()
    section: str | None = None

    for block in blocks:
        if getattr(block, "rows", None):
            if section == "QUOTAS":
                overview.quotas.extend(_quota_rows(block.rows))
            continue

        for line in (block.text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            found = _heading(line)
            if found:
                section = found
                continue

            match = _KEY_VALUE.match(line)
            if match:
                key = match.group(1).lower()
                value = match.group("value")
                if key.startswith("market"):
                    overview.markets = value
                elif "sample" in key:
                    overview.sample_size = _number(value)
                else:
                    overview.loi_minutes = _number(value)
                continue

            if section == "SCREENING REQUIREMENTS":
                overview.screening_requirements.append(line)
            elif section == "GENERAL PROGRAMMING NOTES":
                overview.programming_notes.append(line)

    return overview


def _quota_rows(rows: list[list[str]]) -> list[QuotaTarget]:
    out: list[QuotaTarget] = []
    for row in rows:
        cells = [(c or "").strip() for c in row]
        if not cells or not cells[0]:
            continue
        if _QUOTA_HEADER.search(" ".join(cells)):
            continue  # the header row names the columns
        definition = cells[1] if len(cells) > 1 else ""
        target, low, high = (
            _number(cells[2]) if len(cells) > 2 else None,
            _number(cells[3]) if len(cells) > 3 else None,
            _number(cells[4]) if len(cells) > 4 else None,
        )
        if target is None and low is None and high is None:
            continue  # a spacer row with a label and no numbers
        out.append(
            QuotaTarget(
                group=cells[0], definition=definition,
                target_n=target, min_n=low, max_n=high,
            )
        )
    return out


def unenforced_requirements(overview: StudyOverview, survey: SurveyDefinition) -> list[str]:
    """Screening requirements no question in the instrument appears to enforce.

    The document states who should qualify, so it can be asked whether anything
    makes that true. Matched on the NUMBER a requirement names ("Age 25+",
    "between 10 and 999 employees") against the numbers the instrument screens
    or routes on, because the wording never matches and the number always does.

    Deliberately reported, never acted on: a requirement with no number, or one
    enforced by a quota rather than a terminate, is not a defect — it is a
    question for the person who knows the study.
    """
    screened: set[int] = set()
    screened_labels: list[str] = []
    for question in survey.questions:
        for expression in (question.terminate_if, question.show_if):
            screened.update(_numbers_in(expression))
        for option in question.options or []:
            screened.update(_numbers_in(option.show_if))
        # A threshold is usually written as an ANSWER, not as a number in the
        # condition: the screener terminates on code 7, and code 7 is labelled
        # "$100 million or more". Matching only the condition's numbers reports
        # every such requirement as unenforced, and a check that cries wolf is
        # one a reviewer learns to skip.
        terminating = _codes_in(question.terminate_if, question.qid)
        screened_labels.extend(
            o.label for o in question.options or [] if o.code in terminating
        )

    unmatched: list[str] = []
    for requirement in overview.screening_requirements:
        if _QUOTA_REQUIREMENT.search(requirement):
            continue  # "Max n=100 brick-and-mortar retail only" is a quota, by design
        numbers = {int(n.replace(",", "")) for n in _FIRST_NUMBER.findall(requirement)}
        if not numbers or (numbers & screened):
            continue
        if any(
            str(n) in re.sub(r"[,\s]", "", label)
            for n in numbers
            for label in screened_labels
        ):
            continue  # the screen-out names an option whose label carries the number
        unmatched.append(requirement)
    return unmatched


def _codes_in(expression: Expression | None, qid: str) -> set[int]:
    """Codes a condition tests against its own question."""
    if expression is None:
        return set()
    found: set[int] = set()
    if getattr(expression, "qid", None) == qid:
        found |= _numbers_in(expression)
    for child in expression.children or []:
        found |= _codes_in(child, qid)
    return found


def _numbers_in(expression: Expression | None) -> set[int]:
    if expression is None:
        return set()
    found: set[int] = set()
    if expression.value is not None and isinstance(expression.value, int):
        found.add(expression.value)
    for value in expression.values or []:
        if isinstance(value, int):
            found.add(value)
    for child in expression.children or []:
        found |= _numbers_in(child)
    return found
