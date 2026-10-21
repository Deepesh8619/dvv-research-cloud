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
# The companion text behind an "Other, specify" punch is named
# `<parent>r<code>oe` — the punch code is part of the name. A bare `_OE`
# suffix is a STANDALONE open-end question ("GA_USER_BARRIER_OE": "Please
# describe your specific job responsibilities"), and treating it as a
# companion hid a real question from the reconciliation.
_OTHER_SPECIFY_RE = re.compile(r"r\d+oe$", re.I)
_NO_ANSWER_RE = re.compile(r"^noanswer", re.I)
# HID_/Hid_/h_ name prefixes cover most derived variables, but not all: the
# real export also contains `hC_GEO_ZIP_Flagged`, whose name is just `h` plus
# the question. Its LABEL is unambiguous — "Dummy variable to flag if
# respondent invalid US zip code" — so the declaration in the label is the more
# reliable signal, and the two together miss nothing in the studies seen.
_HIDDEN_NAME_RE = re.compile(r"^(hid[_a-z]*|h_)", re.I)
_HIDDEN_LABEL_RE = re.compile(
    r"\b(hidden|dummy)\s+(variable|question)\b|^\s*hidden\s*(?::|for\b)", re.I
)
_MAXDIFF_NAME_RE = re.compile(r"maxdiff|^md_|(best|worst)$", re.I)
_SYSTEM_NAMES = frozenset(
    {"record", "uuid", "date", "status", "country", "markers", "vos", "start_date",
     # Platform paradata (Decipher et al.): who, when, where and on what device.
     # Read as questions, they were "answered" by every respondent — so anyone
     # screened out at the first question looked as if they had carried on.
     "daytime", "realstart", "laststart", "starttime", "endtime", "ipaddress",
     "useragent", "rid", "vendorid", "otherVariables", "othervariables", "zeroratio",
     "dcua", "md", "q_ans", "geoinfo", "respinfo", "session", "qtime", "list", "url",
     "source", "decLang", "declang", "pid", "psid", "tid", "uniqueid"}
)
# Timers and device/tracking fields, by the shape of the name.
_SYSTEM_NAME_RE = re.compile(
    r"^(question_?time|geoinfor?|respinfor?|fp_|vget|vuser|qinmax\d*_)|_?timer$|"
    # Decipher's typing traces per open end: hQ15OE_KeyStrokeCount, hQ15CopyResponse.
    r"(keystrokecount|charactercount|copyresponse|copy_paste_flagged|speechtotexttext|"
    r"keyboardsuggesttext|manualtyping)$", re.I
)
# dSample, vUserAgent, hCFDeviceWidth: a one-letter lower-case platform prefix
# on a camel-case name. Case-sensitive on purpose: DA_SHARE and dQ5 are questions.
_PLATFORM_CAMEL_RE = re.compile(r"^[dvh][A-Z][A-Za-z]*[a-z][A-Za-z]*$")

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

    if (name.lower() in _SYSTEM_NAMES or _SYSTEM_NAME_RE.search(name)
            or _PLATFORM_CAMEL_RE.match(name)):
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
    if _HIDDEN_NAME_RE.match(name) or _HIDDEN_LABEL_RE.search(lowered_label):
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


# --- Companions -------------------------------------------------------------
# An open-ended question rarely stands alone. The script derives companions from
# it — a band, a threshold flag, a validity check, a refusal marker — and the
# questionnaire's conditions are written against THOSE, not the raw value:
#
#     SS_AD_SPEND   "how much do you spend?"        open number
#     HID_SS_AD_SPEND  "Flag if greater than $1,000,000"   1 = Flag, 2 = Not
#     noanswerSS_AD_SPEND_r99  "Prefer not to answer"      0/1
#
# So "SS_AD_SPEND has no codes" and "the questionnaire lists bands for it" are
# both true and not a contradiction: the bands live in the companion.
#
# Detection is structural, never a list of known names. A companion is a
# non-answerable variable whose name contains an answerable variable's name;
# the LONGEST containing name wins, so `HID_SS_AD_SPEND` attaches to
# `SS_AD_SPEND` rather than to a shorter `SS_AD`.

#: Roles that can act as a companion to a question.
COMPANION_ROLES = frozenset({"hidden", "no_answer", "other_specify"})


@dataclass
class Companion:
    """A derived variable and the question it was computed from."""

    name: str
    role: str
    label: str
    question: str


def find_companions(datamap) -> dict[str, list[Companion]]:
    """Map each question to the variables the script derived from it.

    Returns `{question_name: [Companion, ...]}`. A derived variable that names
    no question is simply absent — a study-level hidden variable like
    `Hid_Segment` belongs to the study, not to one question, and inventing a
    parent for it would be a fabrication.
    """
    roles = {
        v.name: classify_variable(
            v.name, getattr(v, "label", "") or "", getattr(v, "question_ref", None)
        )
        for v in datamap.variables
    }
    # Answerable names, longest first so the most specific parent wins.
    answerable = sorted(
        (name for name, role in roles.items() if role in EXPECTED_IN_QUESTIONNAIRE),
        key=len,
        reverse=True,
    )
    parents = {
        v.name: (getattr(v, "question_ref", None) or v.name) for v in datamap.variables
    }
    by_name = {v.name: v for v in datamap.variables}

    out: dict[str, list[Companion]] = {}
    for name, role in roles.items():
        if role not in COMPANION_ROLES:
            continue
        for candidate in answerable:
            if candidate == name or candidate not in name:
                continue
            owner = parents.get(candidate, candidate)
            out.setdefault(owner, []).append(
                Companion(
                    name=name,
                    role=role,
                    label=getattr(by_name[name], "label", "") or "",
                    question=owner,
                )
            )
            break
    return out
