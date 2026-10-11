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
import re
from dataclasses import dataclass, field
from typing import Any

# Fields worth diffing. Ordered by how much a reviewer cares: the owner
# specifically called out terminates being missed, so they lead. "answers" is
# options, rows and scale taken together — see `_answers`.
DIFFED_FIELDS = ("terminate_if", "show_if", "answers", "text", "type")

# Flags that inform rather than warn. Gaining one is not the rule doing harm:
# a re-read that also reads the study's instructions flags the questions an
# instruction governs, and that scored as "worse" on every such question.
_INFORMATIONAL_FLAGS = frozenset({"BRIEF_RULE"})

# How alike two questions' wording must be to be the same question when their
# numbers differ. A recovered question renumbers everything after it; matched
# by number, Q15 was compared with what had been Q14 and read as a regression.
_SAME_QUESTION = 0.75

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
    # The number this question had before, when the re-read renumbered it.
    renumbered_from: str | None = None

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
        if "regressed" in verdicts or set(self.flags_added) - _INFORMATIONAL_FLAGS:
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
            "renumbered_from": self.renumbered_from,
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
    # Whole-questionnaire quality on each side — see `quality`.
    quality_before: dict = field(default_factory=dict)
    quality_after: dict = field(default_factory=dict)

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
            "quality": {"before": self.quality_before, "after": self.quality_after},
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
                # A renumbered question is listed even if nothing else moved:
                # a new number is a new data column.
                if q.verdict != "unchanged" or q.renumbered_from
            ],
        }


def _labels(items) -> list[str]:
    return [str(i.get("label", "")) for i in items or [] if isinstance(i, dict)]


def _answers(q: dict) -> dict:
    """What a respondent chooses from: options, or a grid's rows and scale.

    Judged together because a grid's answers MOVE between them — a re-read that
    turns a grid's junk options into proper rows and columns lost "options" and
    was scored as a regression while it was the fix.
    """
    out = {}
    for key, name in (("options", "options"), ("rows", "rows"), ("cols", "scale")):
        labels = _labels(q.get(key))
        if labels:
            out[name] = labels
    return out


_RESIDUE = re.compile(r"\[[^\]]*\]|^(?:\s*\|)+|(?:\s*\|)+\s*$")


def _cleaned(answers: dict) -> dict:
    """Answers with scripter notes and table separators taken off."""
    return {k: [" ".join(_RESIDUE.sub(" ", x).split()) for x in v] for k, v in answers.items()}


def _qa_problems(q: dict) -> bool:
    """Does the QA agent find anything wrong, judged now on this reading?

    Not the stored QA_ISSUE flag: an older reading was never QA'd, so it carries
    no flag, and a fresh reading's flag would read as the rule doing harm.
    """
    from src.core.survey_definition import Question
    from src.engines.questionnaire.qa_loop import _tested, qa_check

    question = Question.from_dict(q)
    return _tested(question) and bool(qa_check(question, {question.qid: question}))


def _answerable(q: dict) -> bool:
    from src.core.survey_definition import Question, QuestionType, has_nothing_to_answer

    question = Question.from_dict(q)
    return question.type is QuestionType.INFO or not has_nothing_to_answer(question)


def _diff_question(before: dict, after: dict) -> QuestionDiff:
    diff = QuestionDiff(qid=after.get("qid") or before.get("qid") or "?")
    if before.get("qid") != after.get("qid"):
        diff.renumbered_from = before.get("qid")

    for name in DIFFED_FIELDS:
        if name == "answers":
            b, a = _answers(before), _answers(after)
            if _norm(b) == _norm(a):
                continue
            was, now = _answerable(before), _answerable(after)
            verdict = (
                "improved" if now and not was
                else "regressed" if was and not now
                # Same answers once separators and notes are taken off: the
                # respondent no longer reads "Safety |". A cleanup, not a guess.
                else "improved" if _norm(_cleaned(b)) == _norm(_cleaned(a))
                else "changed"
            )
            diff.changes.append(FieldChange(name, verdict, b, a))
            continue
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

    b_flags = set(before.get("flags") or []) - {"QA_ISSUE"}
    a_flags = set(after.get("flags") or []) - {"QA_ISSUE"}
    if _qa_problems(before):
        b_flags.add("QA_ISSUE")
    if _qa_problems(after):
        a_flags.add("QA_ISSUE")
    diff.flags_cleared = sorted(b_flags - a_flags)
    diff.flags_added = sorted(a_flags - b_flags)
    return diff


def _wording(q: dict) -> str:
    return " ".join((q.get("text") or "").lower().split())[:240]


def _pair(before: list[dict], after: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Match each question to itself across the two readings.

    By number when the wording agrees, then by wording among the rest — so a
    question that was renumbered is still compared with itself.
    """
    from difflib import SequenceMatcher

    def alike(b: dict, a: dict) -> float:
        return SequenceMatcher(None, _wording(b), _wording(a)).ratio()

    pairs: list[tuple[dict | None, dict | None]] = []
    a_by_id = {q.get("qid"): q for q in after if q.get("qid")}
    left_b: list[dict] = []
    used: set[int] = set()
    for b in before:
        a = a_by_id.get(b.get("qid"))
        if a is not None and id(a) not in used and (
            not _wording(b) or alike(b, a) >= _SAME_QUESTION
        ):
            pairs.append((b, a))
            used.add(id(a))
        else:
            left_b.append(b)
    left_a = [a for a in after if id(a) not in used]
    unmatched: list[dict] = []
    for b in left_b:
        best = max(left_a, key=lambda a: alike(b, a), default=None)
        if best is not None and alike(b, best) >= _SAME_QUESTION:
            pairs.append((b, best))
            left_a.remove(best)
        else:
            unmatched.append(b)
    # Same number, wording rewritten, and nothing elsewhere reads like it: the
    # same question with new text — "changed", never "lost one, found one",
    # which would score a rewrite as a recovery.
    for b in unmatched:
        same = next((a for a in left_a if a.get("qid") == b.get("qid")), None)
        if same is not None:
            pairs.append((b, same))
            left_a.remove(same)
        else:
            pairs.append((b, None))
    pairs.extend((None, a) for a in left_a)
    return pairs


def quality(questions: list[dict]) -> dict:
    """How good one reading of the questionnaire is, in two percentages.

    answerable  questions a respondent can actually answer (display text counts)
    qa_pass     questions the QA agent finds nothing wrong with — as the link
                shows them, and against the document

    Both are measured the same way on both sides, so the two numbers are a fair
    before/after — unlike the per-question verdicts, which are heuristics.
    """
    from src.core.survey_definition import Question
    from src.engines.questionnaire.qa_loop import _tested, qa_check

    parsed = [Question.from_dict(q) for q in questions if q.get("qid")]
    if not parsed:
        return {"n": 0, "answerable_pct": None, "qa_pass_pct": None}
    by_qid = {q.qid: q for q in parsed}
    tested = [q for q in parsed if _tested(q)]
    answerable = sum(1 for q in parsed if _answerable(q.to_dict()))
    passing = sum(1 for q in tested if not qa_check(q, by_qid))
    return {
        "n": len(parsed),
        "answerable_pct": round(100 * answerable / len(parsed)),
        "qa_pass_pct": round(100 * passing / len(tested)) if tested else None,
    }


def diff_extractions(
    before: list[dict], after: list[dict]
) -> ExtractionDiff:
    """Diff two question lists of the same document, question by question.

    Both sides are plain dicts (a SurveyDefinition's serialized `questions`),
    so this works equally on a stored definition and a fresh candidate
    extraction without either being loaded into engine objects.
    """
    diffs: list[QuestionDiff] = []
    for b, a in _pair(before, after):
        if b is None:
            diffs.append(QuestionDiff(qid=a.get("qid") or "?", only_in="after"))
        elif a is None:
            diffs.append(QuestionDiff(qid=b.get("qid") or "?", only_in="before"))
        else:
            diffs.append(_diff_question(b, a))
    return ExtractionDiff(
        questions=diffs, quality_before=quality(before), quality_after=quality(after)
    )
