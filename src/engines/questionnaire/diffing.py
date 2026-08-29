"""Compare two extractions of the same document, question by question.

This is what turns "Vishal wrote one sentence" into "41 questions improved, 6
regressed" — the screen that makes the correction loop trustworthy instead of
a black box.

The honest part: without a gold standard there is no ground truth, so
`improved` / `regressed` are HEURISTICS, not facts. They are directional
signals to point a reviewer at what changed, and the labels are chosen
conservatively:

  improved   a field went from absent to present, or a flag cleared
  regressed  a field went from present to absent, or a flag appeared
  changed    both sides have a value and they differ — genuinely ambiguous,
             so it is never scored as a win

That last rule matters. A rule that rewrites 40 question texts into different
wrong text would look like a triumph under a naive "it changed, so it worked"
metric. Here it reads as 40 `changed`, which is exactly the "look at this
yourself" signal a reviewer needs.

Pure functions over plain dicts — no LLM, no database — so the whole thing is
testable without either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Fields worth diffing. Ordered by how much a reviewer cares: the owner
# specifically called out terminates being missed, so they lead.
DIFFED_FIELDS = ("terminate_if", "show_if", "options", "text", "type")

# A field being absent covers all the shapes "nothing here" arrives in.
_EMPTY = (None, "", [], {})


def _present(value: Any) -> bool:
    """True when a field actually carries content."""
    if isinstance(value, str):
        return bool(value.strip())
    return value not in _EMPTY and value is not None


def _norm(value: Any) -> Any:
    """Comparable form: dicts/lists compare by serialized shape, text by words."""
    if isinstance(value, str):
        return " ".join(value.split()).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


@dataclass
class FieldChange:
    """One field of one question that moved between the two extractions."""

    field: str
    verdict: str  # improved | regressed | changed
    before: Any
    after: Any


@dataclass
class QuestionDiff:
    qid: str
    changes: list[FieldChange] = field(default_factory=list)
    flags_cleared: list[str] = field(default_factory=list)
    flags_added: list[str] = field(default_factory=list)
    # Present in one extraction only — a question appearing or vanishing is the
    # single most consequential change a rule can cause, so it is tracked apart
    # from field-level edits.
    only_in: str | None = None  # "before" | "after"

    @property
    def verdict(self) -> str:
        """Worst-case summary for the question. Regressions dominate.

        A question that gained a terminate but lost its options is NOT a win;
        surfacing it as one would let a harmful rule through review.
        """
        if self.only_in == "before":
            return "regressed"  # the rule made a question disappear
        if self.only_in == "after":
            return "improved"  # the rule recovered a question
        verdicts = {c.verdict for c in self.changes}
        if "regressed" in verdicts or self.flags_added:
            return "regressed"
        if "changed" in verdicts:
            return "changed"
        if "improved" in verdicts or self.flags_cleared:
            return "improved"
        return "unchanged"

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "verdict": self.verdict,
            "only_in": self.only_in,
            "flags_cleared": self.flags_cleared,
            "flags_added": self.flags_added,
            "changes": [
                {
                    "field": c.field,
                    "verdict": c.verdict,
                    "before": c.before,
                    "after": c.after,
                }
                for c in self.changes
            ],
        }


@dataclass
class ExtractionDiff:
    questions: list[QuestionDiff]

    def counts(self) -> dict[str, int]:
        out = {"improved": 0, "regressed": 0, "changed": 0, "unchanged": 0}
        for q in self.questions:
            out[q.verdict] += 1
        return out

    def to_dict(self) -> dict:
        counts = self.counts()
        return {
            "counts": counts,
            "n_questions": len(self.questions),
            # Regressions first and never collapsible in the UI: a rule that
            # fixes 40 and breaks 6 must not read as an unqualified success.
            "questions": [
                q.to_dict()
                for q in sorted(
                    self.questions,
                    key=lambda q: {"regressed": 0, "changed": 1, "improved": 2}.get(
                        q.verdict, 3
                    ),
                )
                if q.verdict != "unchanged"
            ],
        }


def _diff_question(before: dict, after: dict) -> QuestionDiff:
    diff = QuestionDiff(qid=before.get("qid") or after.get("qid") or "?")

    for name in DIFFED_FIELDS:
        b, a = before.get(name), after.get(name)
        bp, ap = _present(b), _present(a)
        if not bp and not ap:
            continue
        if _norm(b) == _norm(a):
            continue
        if not bp and ap:
            verdict = "improved"  # found something that was missing
        elif bp and not ap:
            verdict = "regressed"  # lost something we had
        else:
            verdict = "changed"  # both present and different — human call
        diff.changes.append(FieldChange(name, verdict, b, a))

    b_flags = set(before.get("flags") or [])
    a_flags = set(after.get("flags") or [])
    diff.flags_cleared = sorted(b_flags - a_flags)
    diff.flags_added = sorted(a_flags - b_flags)
    return diff


def diff_extractions(
    before: list[dict], after: list[dict]
) -> ExtractionDiff:
    """Diff two question lists of the same document, keyed on qid.

    Both sides are plain dicts (a SurveyDefinition's serialized `questions`),
    so this works equally on a stored definition and a fresh candidate
    extraction without either being loaded into engine objects.
    """
    b_by_id = {q.get("qid"): q for q in before if q.get("qid")}
    a_by_id = {q.get("qid"): q for q in after if q.get("qid")}

    diffs: list[QuestionDiff] = []
    for qid in b_by_id.keys() | a_by_id.keys():
        b, a = b_by_id.get(qid), a_by_id.get(qid)
        if b is None:
            diffs.append(QuestionDiff(qid=qid, only_in="after"))
        elif a is None:
            diffs.append(QuestionDiff(qid=qid, only_in="before"))
        else:
            diffs.append(_diff_question(b, a))
    return ExtractionDiff(questions=diffs)
