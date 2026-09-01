"""What each column in a survey export is FOR.

A data file is not a flat table of questions. A 440-column client export holds
answers, the machinery that produced them, and the bookkeeping around them —
and telling a reviewer "412 variables no question claimed" is useless, because
almost none of those are a problem. Most are doing exactly what they should.

The roles below are the ones that actually occur in Decipher/Confirmit exports,
verified against a real 750-respondent study:

    question        a plain answer column
    punch           one option of a multi-select, e.g. DA_GOALSr1
    other_specify   the free text behind an "Other, specify" punch
    no_answer       the companion flag for a refused/skipped answer
    hidden          a value the script computes — segment, quota group, device
    maxdiff         MaxDiff design and response columns
    qa_flag         a straightlining / quality virtual variable
    system          record id, uuid, timestamp, disposition

Only `question` and `punch` are expected to appear in the questionnaire. A
hidden variable is SUPPOSED to be absent from it — it is computed, not asked —
so reporting it as "unmatched" invites someone to go looking for a question
that was never meant to exist. Hidden variables still matter: they carry the
segment and quota logic a client analyses by, which is why they get their own
role rather than being lumped in with noise.

Classification is by name and label convention only. No model, no data read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Decipher writes one column per option as `<parent>r<n>`; a grid adds `c<n>`.
_PUNCH_RE = re.compile(r"^(?P<parent>\w+?)r\d+$")
_OTHER_SPECIFY_RE = re.compile(r"r?\d*oe$", re.I)
_NO_ANSWER_RE = re.compile(r"^noanswer", re.I)
# HID_/Hid_/h_ prefixes plus the word "hidden" in the label — both appear.
_HIDDEN_NAME_RE = re.compile(r"^(hid[_a-z]*|h_)", re.I)
_MAXDIFF_NAME_RE = re.compile(r"maxdiff|^md_|(best|worst)$", re.I)
_SYSTEM_NAMES = frozenset(
    {"record", "uuid", "date", "status", "country", "markers", "vos", "start_date"}
)

ROLE_LABELS = {
    "question": "Question",
    "punch": "Answer option of a multi-select",
    "other_specify": "“Other, specify” text",
    "no_answer": "Refused / no-answer flag",
    "hidden": "Hidden variable computed by the script",
    "maxdiff": "MaxDiff design or response",
    "qa_flag": "Quality-check flag",
    "system": "Survey system column",
}

# Roles a reviewer should expect to find in the questionnaire. Everything else
# is legitimately absent from it and must not be reported as a gap.
EXPECTED_IN_QUESTIONNAIRE = frozenset({"question", "punch"})


@dataclass
class RoleSummary:
    """Counts and members per role, for a whole datamap."""

    roles: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {role: len(names) for role, names in self.roles.items() if names}

    def of(self, role: str) -> list[str]:
        return self.roles.get(role, [])


def classify_variable(name: str, label: str = "", question_ref: str | None = None) -> str:
    """The role one column plays. Order matters — the tests pin the precedence."""
    lowered_label = (label or "").lower()

    if name.lower() in _SYSTEM_NAMES:
        return "system"
    if "virtual variable" in lowered_label:
        return "qa_flag"
    # MaxDiff before punch: MAXDIFF_LIKELY_MD_1best would otherwise look like a
    # plain question, and MD_Items_1 like a punch.
    if _MAXDIFF_NAME_RE.search(name):
        return "maxdiff"
    if _NO_ANSWER_RE.match(name):
        return "no_answer"
    if _OTHER_SPECIFY_RE.search(name):
        return "other_specify"
    # Hidden before punch: HID_ names never carry a parent, but a label saying
    # "hidden" on a punch-shaped name should still read as hidden.
    if _HIDDEN_NAME_RE.match(name) or lowered_label.startswith("hidden"):
        return "hidden"
    if question_ref or _PUNCH_RE.match(name):
        return "punch"
    return "question"


def summarize(datamap) -> RoleSummary:
    """Group every variable in a datamap by the role it plays."""
    summary = RoleSummary(roles={role: [] for role in ROLE_LABELS})
    for variable in datamap.variables:
        role = classify_variable(
            variable.name,
            getattr(variable, "label", "") or "",
            getattr(variable, "question_ref", None),
        )
        summary.roles[role].append(variable.name)
    return summary


def unexplained(datamap, matched_variables: set[str]) -> list[str]:
    """Columns a reviewer should actually chase.

    A variable is only worth reporting when it plays a role that BELONGS in the
    questionnaire and no question claimed it. Hidden variables, MaxDiff design
    columns, QA flags and system columns are all correctly absent from a
    questionnaire and are filtered out here — listing them turns a short,
    actionable list into 400 lines nobody reads.
    """
    out = []
    for variable in datamap.variables:
        if variable.name in matched_variables:
            continue
        role = classify_variable(
            variable.name,
            getattr(variable, "label", "") or "",
            getattr(variable, "question_ref", None),
        )
        if role in EXPECTED_IN_QUESTIONNAIRE:
            out.append(variable.name)
    return sorted(out)
