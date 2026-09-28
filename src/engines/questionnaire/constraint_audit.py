"""Can a respondent satisfy the constraints this questionnaire puts on them?

A condition that cannot fire is audited before fielding (qc/condition_audit).
A CONSTRAINT that cannot be satisfied was not, and it fails in a worse place:
the respondent meets it, cannot get past it, and abandons. That arrives as
drop-off on a page — which looks like a boring question, not a bug — and the
interview it cost is already paid for.

Every check here is a thing that is impossible or self-defeating on its own
terms, provable from the definition with no data and no model:

    impossible_select   a floor above the ceiling; no answer can satisfy both
    cap_exceeds_list    "select up to 5" over three options — the cap is a lie
    floor_exceeds_list  "select at least 5" over three; nobody can proceed
    inverted_range      a numeric minimum above its maximum
    blocked_opt_out     the way out ("99 Prefer not to answer") is outside the
                        range the question will accept, so the opt-out is a
                        dead end

`cap_exceeds_list` is a warning — it wastes nothing, it just means the sentence
on screen promises a choice the list cannot offer. The rest reject an answer a
respondent may reasonably give, so they are failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.survey_definition import Question, QuestionType, SurveyDefinition

#: Wording of the standard ways out. Matched on the option LABEL, because the
#: code for "don't know" is a house convention (99, 98, -777) and differs per
#: client, while the words barely vary.
_WAY_OUT = re.compile(
    r"\b(?:don'?t\s+know|dk\b|not\s+sure|unsure|no\s+opinion|can'?t\s+say|"
    r"prefer\s+not|refus|decline|not\s+applicable|\bn/?a\b|none\s+of\s+(?:these|the\s+above))",
    re.I,
)


@dataclass
class ConstraintIssue:
    """One constraint a respondent cannot satisfy."""

    kind: str
    qid: str
    detail: str
    severity: str = "fail"


def _opt_out_codes(question: Question) -> list[int]:
    return [
        o.code for o in question.options or []
        if o.code is not None and _WAY_OUT.search(o.label or "")
    ]


def _as_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def audit_constraints(survey: SurveyDefinition) -> list[ConstraintIssue]:
    """Every constraint in the instrument that cannot be met, or has no way out."""
    issues: list[ConstraintIssue] = []
    for question in survey.questions:
        if question.type is QuestionType.INFO:
            continue
        validation = question.validation or {}
        options = question.options or []
        floor, ceiling = validation.get("min_select"), validation.get("max_select")
        low, high = _as_number(validation.get("min")), _as_number(validation.get("max"))

        if isinstance(floor, int) and isinstance(ceiling, int) and floor > ceiling:
            issues.append(ConstraintIssue(
                "impossible_select", question.qid,
                f"must pick at least {floor} but at most {ceiling} — no answer satisfies both",
            ))
        if isinstance(floor, int) and options and floor > len(options):
            issues.append(ConstraintIssue(
                "floor_exceeds_list", question.qid,
                f"must pick at least {floor} of only {len(options)} options — "
                "nobody can get past it",
            ))
        if isinstance(ceiling, int) and options and ceiling > len(options):
            issues.append(ConstraintIssue(
                "cap_exceeds_list", question.qid,
                f"allows up to {ceiling} of only {len(options)} options — the cap "
                "does nothing, and the wording tells the respondent otherwise",
                severity="warn",
            ))
        if low is not None and high is not None and low > high:
            issues.append(ConstraintIssue(
                "inverted_range", question.qid,
                f"accepts a number at least {low} and at most {high} — every "
                "answer is rejected",
            ))

        # A number box whose opt-out code sits outside the range it will accept.
        # The respondent is offered a way out that the validator then refuses.
        if question.type is QuestionType.NUMERIC and (low is not None or high is not None):
            for code in _opt_out_codes(question):
                if (low is not None and code < low) or (high is not None and code > high):
                    issues.append(ConstraintIssue(
                        "blocked_opt_out", question.qid,
                        f"offers code {code} as a way out, but only accepts "
                        f"{low if low is not None else '−∞'} to "
                        f"{high if high is not None else '∞'} — the way out is refused",
                    ))

        # ponytail: "a required question with no way out" belongs here in
        # principle — somebody who cannot answer guesses, and the guess is
        # indistinguishable from an answer. TRIED and removed: now that
        # `required` is real it fires on 25, 21 and 18 questions across the
        # three client documents, and the ones it names are exhaustive by
        # construction ("Are you the parent of any teenagers? Yes/No", three
        # mutually-exclusive self-descriptions). Whether not-knowing is
        # PLAUSIBLE is the actual test and it is not in the option list. A
        # check that names 40% of a questionnaire is one a reviewer learns to
        # scroll past, which costs more than it finds.
    return issues


if __name__ == "__main__":  # self-check
    from src.core.survey_definition import Option

    def _q(**kw):
        kw.setdefault("text", "t")
        kw.setdefault("type", QuestionType.MULTI)
        return Question(**kw)

    three = [Option(code=i, label=f"O{i}") for i in (1, 2, 3)]
    kinds = lambda s: sorted(i.kind for i in audit_constraints(s))  # noqa: E731
    s = SurveyDefinition(study_id="t", questions=[
        _q(qid="A", options=three, validation={"min_select": 3, "max_select": 2}),
        _q(qid="B", options=three, validation={"max_select": 5}),
        _q(qid="C", type=QuestionType.NUMERIC,
           options=[Option(code=99, label="Prefer not to answer")],
           validation={"min": 0, "max": 50}),
        _q(qid="D", options=three, validation={"max_select": 2}),  # fine
    ])
    assert kinds(s) == ["blocked_opt_out", "cap_exceeds_list", "impossible_select"], kinds(s)
    clean = SurveyDefinition(study_id="t", questions=[_q(qid="D", options=three)])
    assert audit_constraints(clean) == []
    print("ok")
