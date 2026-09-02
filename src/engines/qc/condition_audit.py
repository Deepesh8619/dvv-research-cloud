"""Can this condition ever fire, given what was actually scripted?

A routing or screen-out condition is only meaningful if the variable it names
exists in the data map and the codes it tests are real punches on that
variable. When they are not, the condition is dead: it evaluates false for
every respondent, for ever.

That failure is invisible in the obvious place. Run the data check and a dead
screen-out reports **zero violations**, which reads exactly like a screen-out
nobody breached. The two must never look the same — it is the same shape of
bug as a terminate whose codes were written to the wrong field, where 16 of 17
screen-outs silently matched nobody.

So conditions are audited against the data map BEFORE any respondent row is
read:

    unknown_variable   the condition names a question with no column at all
    unscriptable_code  the codes tested are not punches on that variable
    no_codes           an `in` test with an empty value list

Deterministic; no model, no data file needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.models import DataMap
from src.core.survey_definition import Expression, QuestionVariableLink, SurveyDefinition


@dataclass
class ConditionIssue:
    """One condition that cannot do what it says."""

    qid: str
    #: show_if | terminate_if | mask
    where: str
    kind: str
    detail: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "where": self.where,
            "kind": self.kind,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass
class ConditionAudit:
    issues: list[ConditionIssue] = field(default_factory=list)
    n_checked: int = 0

    @property
    def passed(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "n_checked": self.n_checked,
            "issues": [i.to_dict() for i in self.issues],
        }


def _codes_for(datamap: DataMap, links: dict[str, list[str]], qid: str) -> set[int] | None:
    """Punches available on the variables a question maps to.

    None means the question has no column at all. An empty set means it has
    columns but no coded values — an open response, where testing a code is
    meaningless rather than merely wrong.
    """
    names = links.get(qid)
    if not names:
        return None
    by_name = {v.name: v for v in datamap.variables}
    codes: set[int] = set()
    seen = False
    for name in names:
        variable = by_name.get(name)
        if variable is None:
            continue
        seen = True
        codes |= set(variable.values or {})
        # A punch column IS the option: `<parent>r<code>` carries its own code.
        suffix = name[len(qid):].lstrip("rR")
        if suffix.isdigit():
            codes.add(int(suffix))
    return codes if seen else None


def _leaves(expression: Expression) -> list[Expression]:
    """Every comparison in an expression tree, ignoring the logical joins."""
    if expression.children:
        out: list[Expression] = []
        for child in expression.children:
            out.extend(_leaves(child))
        return out
    return [expression]


def _audit_expression(
    qid: str,
    where: str,
    expression: Expression,
    datamap: DataMap,
    links: dict[str, list[str]],
    system_codes: frozenset[int],
) -> list[ConditionIssue]:
    issues: list[ConditionIssue] = []
    for leaf in _leaves(expression):
        if leaf.qid is None:
            continue
        available = _codes_for(datamap, links, leaf.qid)
        if available is None:
            issues.append(
                ConditionIssue(
                    qid=qid,
                    where=where,
                    kind="unknown_variable",
                    detail=(
                        f"{qid}'s {where} tests {leaf.qid}, which has no column in the "
                        "data map. The condition can never be true."
                    ),
                )
            )
            continue

        tested = set()
        if leaf.values:
            tested |= {int(v) for v in leaf.values if isinstance(v, (int, float))}
        if isinstance(leaf.value, (int, float)):
            tested.add(int(leaf.value))

        if leaf.op == "in" and not tested:
            issues.append(
                ConditionIssue(
                    qid=qid,
                    where=where,
                    kind="no_codes",
                    detail=(
                        f"{qid}'s {where} tests membership in an empty code list, so it "
                        "matches nobody."
                    ),
                )
            )
            continue

        if not available:
            continue  # open response — a code test is meaningless, not wrong
        impossible = sorted(tested - available - system_codes)
        if impossible and not (tested & available):
            issues.append(
                ConditionIssue(
                    qid=qid,
                    where=where,
                    kind="unscriptable_code",
                    detail=(
                        f"{qid}'s {where} tests {leaf.qid} for {impossible}, but "
                        f"{leaf.qid} was scripted with punches {sorted(available)[:10]}. "
                        "No respondent can satisfy it."
                    ),
                )
            )
        elif impossible:
            issues.append(
                ConditionIssue(
                    qid=qid,
                    where=where,
                    kind="unscriptable_code",
                    severity="warning",
                    detail=(
                        f"{qid}'s {where} tests {leaf.qid} for {impossible}, which "
                        f"{leaf.qid} does not have. The rest of the condition still fires."
                    ),
                )
            )
    return issues


def audit_conditions(
    survey: SurveyDefinition,
    links: list[QuestionVariableLink],
    datamap: DataMap,
    system_codes: frozenset[int] = frozenset({0, -1, 98, 99, 999, -99}),
) -> ConditionAudit:
    """Check every routing, screen-out and mask against what was scripted."""
    link_map = {link.qid: link.variable_names for link in links if link.variable_names}
    audit = ConditionAudit()

    for question in survey.questions:
        for where, expression in (
            ("show_if", question.show_if),
            ("terminate_if", question.terminate_if),
        ):
            if expression is None:
                continue
            audit.n_checked += 1
            audit.issues.extend(
                _audit_expression(
                    question.qid, where, expression, datamap, link_map, system_codes
                )
            )

        masks = ((question.validation or {}).get("techniques") or {}).get("masks") or []
        for mask in masks:
            source = mask.get("source_qid")
            if not source:
                continue
            audit.n_checked += 1
            if _codes_for(datamap, link_map, source) is None:
                audit.issues.append(
                    ConditionIssue(
                        qid=question.qid,
                        where="mask",
                        kind="unknown_variable",
                        detail=(
                            f"{question.qid}'s options are masked from {source}, which has "
                            "no column in the data map — the mask cannot be verified."
                        ),
                        severity="warning",
                    )
                )

    # Worst first, then stable by question.
    audit.issues.sort(key=lambda i: (i.severity != "error", i.qid))
    return audit
