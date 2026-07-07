"""Survey instrument model — the questionnaire as designed.

Where `models.py`/`DataMap` describes *collected data as it exists in a file*,
this module describes *the survey instrument as it was authored*: questions,
routing logic, blocks, quotas. It is deliberately kept separate from `DataMap`
and connected only through `QuestionVariableLink`.

This will later power LLM extraction of Word/PDF questionnaires, an authoring
UI, a fielding runtime, and a SurveyJS compiler. This module is pure in-memory
types plus validation — no I/O, no LLM calls, no routes.

Design note — Expression is a safe, serializable condition AST, never raw code.
There is no `eval` and no embedded Python: routing is data, not behaviour.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

SCHEMA_VERSION = "1.0"


class QuestionType(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
    GRID_SINGLE = "grid_single"
    NUMERIC = "numeric"
    OPEN_TEXT = "open_text"
    DROPDOWN = "dropdown"
    RANK = "rank"
    INFO = "info"  # non-question display content


# String literals reused across entities. Kept as module constants so callers and
# tests share one source of truth without introducing a second enum pattern.
ExprOp = Literal["eq", "ne", "in", "gt", "lt", "and", "or", "not", "selected"]
_LOGICAL_OPS = frozenset({"and", "or", "not"})


@dataclass
class Expression:
    """A safe, serializable routing condition.

    Leaf ops (``eq``/``ne``/``gt``/``lt``) compare a question's answer against
    ``value``; ``in`` tests membership in ``values``; ``selected`` tests whether
    an option is chosen in a multi. Logical ops (``and``/``or``/``not``) nest
    ``children`` and ignore ``qid``/``value``.

    ``verbatim_text`` preserves the routing prose this was derived from so a
    human can always compare the structured form to the original wording.
    """

    op: ExprOp
    qid: str | None = None
    value: Any = None
    values: list[Any] | None = None
    children: list[Expression] | None = None
    verbatim_text: str | None = None

    def referenced_qids(self) -> set[str]:
        """Every qid mentioned anywhere in this expression tree."""
        found: set[str] = set()
        if self.qid is not None:
            found.add(self.qid)
        for child in self.children or []:
            found |= child.referenced_qids()
        return found

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op}
        if self.qid is not None:
            out["qid"] = self.qid
        if self.value is not None:
            out["value"] = self.value
        if self.values is not None:
            out["values"] = self.values
        if self.children is not None:
            out["children"] = [c.to_dict() for c in self.children]
        if self.verbatim_text is not None:
            out["verbatim_text"] = self.verbatim_text
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Expression:
        children = raw.get("children")
        return cls(
            op=raw["op"],
            qid=raw.get("qid"),
            value=raw.get("value"),
            values=raw.get("values"),
            children=[cls.from_dict(c) for c in children] if children is not None else None,
            verbatim_text=raw.get("verbatim_text"),
        )


def _as_selected_set(answer: Any) -> set[Any]:
    """Normalise a stored answer into a set of chosen codes.

    A single-select stores one scalar code; a multi-select stores a list. Both
    are treated uniformly as "the set of codes this respondent chose" so the
    ``in``/``selected`` ops work regardless of question type.
    """
    if answer is None:
        return set()
    if isinstance(answer, (list, tuple, set)):
        return set(answer)
    return {answer}


def evaluate(expression: Expression, answers: dict[str, Any]) -> bool:
    """Evaluate a routing ``Expression`` against a respondent's answers.

    Pure function of ``(expression, answers) -> bool``: no I/O, no side effects,
    no ``eval``. ``answers`` maps ``qid -> value`` where a single-select value is
    a scalar code and a multi-select value is a list of codes.

    Missing / unanswered qids degrade safely: a leaf op over an absent qid
    yields ``False`` (the condition is simply "not satisfied") rather than
    raising, because routing must survive incomplete data — real respondent
    records have skip-logic gaps by design.
    """
    op = expression.op

    if op == "and":
        return all(evaluate(c, answers) for c in expression.children or [])
    if op == "or":
        return any(evaluate(c, answers) for c in expression.children or [])
    if op == "not":
        # `not` over an empty child list is vacuously True (nothing to negate);
        # normally it wraps exactly one child.
        return not any(evaluate(c, answers) for c in expression.children or [])

    # Leaf ops need an answered qid; treat unanswered as "not satisfied".
    qid = expression.qid
    if qid is None or qid not in answers:
        return False
    answer = answers[qid]
    if answer is None:
        return False

    if op == "selected":
        return expression.value in _as_selected_set(answer)
    if op == "in":
        chosen = _as_selected_set(answer)
        return any(v in chosen for v in (expression.values or []))

    if op == "eq":
        return answer == expression.value
    if op == "ne":
        return answer != expression.value

    # Numeric comparisons: guard against non-comparable / missing operands.
    if op in ("gt", "lt"):
        target = expression.value
        if target is None:
            return False
        try:
            if op == "gt":
                return answer > target
            return answer < target
        except TypeError:
            return False

    raise ValueError(f"unknown expression op: {op!r}")


@dataclass
class Option:
    """One answer choice, grid row, or grid column."""

    code: int
    label: str
    exclusive: bool = False  # selecting this deselects all others (e.g. "None of these")
    anchored: bool = False  # pinned in place when options are randomized
    open_other: bool = False  # attaches an open-text "Other, specify" field

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Option:
        return cls(**raw)


@dataclass
class Question:
    """One question of the instrument, plus extraction provenance."""

    qid: str
    text: str
    type: QuestionType = QuestionType.SINGLE
    options: list[Option] = field(default_factory=list)
    rows: list[Option] | None = None  # grid rows
    cols: list[Option] | None = None  # grid columns
    required: bool = True
    randomize_options: bool = False
    show_if: Expression | None = None
    terminate_if: Expression | None = None
    piping_refs: list[str] = field(default_factory=list)  # qids piped into text
    validation: dict[str, Any] | None = None  # numeric range, multi min/max, etc.
    # Provenance — populated when this question was extracted from a source doc.
    source_text: str | None = None  # verbatim excerpt
    source_location: str | None = None  # page / paragraph reference
    extraction_confidence: dict[str, float] = field(default_factory=dict)  # per-field
    flags: list[str] = field(default_factory=list)  # e.g. "AMBIGUOUS_ROUTING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "text": self.text,
            "type": self.type.value,
            "options": [o.to_dict() for o in self.options],
            "rows": [o.to_dict() for o in self.rows] if self.rows is not None else None,
            "cols": [o.to_dict() for o in self.cols] if self.cols is not None else None,
            "required": self.required,
            "randomize_options": self.randomize_options,
            "show_if": self.show_if.to_dict() if self.show_if is not None else None,
            "terminate_if": self.terminate_if.to_dict()
            if self.terminate_if is not None
            else None,
            "piping_refs": self.piping_refs,
            "validation": self.validation,
            "source_text": self.source_text,
            "source_location": self.source_location,
            "extraction_confidence": self.extraction_confidence,
            "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Question:
        rows = raw.get("rows")
        cols = raw.get("cols")
        show_if = raw.get("show_if")
        terminate_if = raw.get("terminate_if")
        return cls(
            qid=raw["qid"],
            text=raw["text"],
            type=QuestionType(raw["type"]),
            options=[Option.from_dict(o) for o in raw.get("options", [])],
            rows=[Option.from_dict(o) for o in rows] if rows is not None else None,
            cols=[Option.from_dict(o) for o in cols] if cols is not None else None,
            required=raw.get("required", True),
            randomize_options=raw.get("randomize_options", False),
            show_if=Expression.from_dict(show_if) if show_if is not None else None,
            terminate_if=Expression.from_dict(terminate_if)
            if terminate_if is not None
            else None,
            piping_refs=raw.get("piping_refs", []),
            validation=raw.get("validation"),
            source_text=raw.get("source_text"),
            source_location=raw.get("source_location"),
            extraction_confidence=raw.get("extraction_confidence", {}),
            flags=raw.get("flags", []),
        )


@dataclass
class Block:
    """An ordered group of questions with optional display condition."""

    block_id: str
    label: str = ""
    question_ids: list[str] = field(default_factory=list)
    randomize_questions: bool = False
    show_if: Expression | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "label": self.label,
            "question_ids": self.question_ids,
            "randomize_questions": self.randomize_questions,
            "show_if": self.show_if.to_dict() if self.show_if is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Block:
        show_if = raw.get("show_if")
        return cls(
            block_id=raw["block_id"],
            label=raw.get("label", ""),
            question_ids=raw.get("question_ids", []),
            randomize_questions=raw.get("randomize_questions", False),
            show_if=Expression.from_dict(show_if) if show_if is not None else None,
        )


@dataclass
class FlowNode:
    """One step in the survey flow: enter a block, check quotas, or end."""

    type: Literal["block", "quota_check", "end"]
    block_id: str | None = None
    status: Literal["complete", "terminate", "overquota"] | None = None  # only for "end"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FlowNode:
        return cls(
            type=raw["type"],
            block_id=raw.get("block_id"),
            status=raw.get("status"),
        )


@dataclass
class QuotaSheet:
    """Stub — full quota-cell enforcement arrives in a later task.

    Each dimension dict carries ``cell_label: str``, ``condition: Expression``,
    ``limit: int``, ``mode: Literal["hard", "soft"]``.
    """

    name: str
    dimensions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dimensions": [self._dim_to_dict(d) for d in self.dimensions],
        }

    @staticmethod
    def _dim_to_dict(dim: dict[str, Any]) -> dict[str, Any]:
        out = dict(dim)
        cond = out.get("condition")
        if isinstance(cond, Expression):
            out["condition"] = cond.to_dict()
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuotaSheet:
        dims = []
        for dim in raw.get("dimensions", []):
            dim = dict(dim)
            cond = dim.get("condition")
            if isinstance(cond, dict):
                dim["condition"] = Expression.from_dict(cond)
            dims.append(dim)
        return cls(name=raw["name"], dimensions=dims)


@dataclass
class EmbeddedField:
    """A non-question data field carried on the respondent record."""

    name: str
    source: Literal["url_param", "computed"] = "url_param"
    default: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EmbeddedField:
        return cls(**raw)


@dataclass
class QuestionVariableLink:
    """Join between a designed question and collected `DataMap` variables.

    Kept separate from SurveyDefinition on purpose: one question can map to many
    variables (a multi explodes to Q5_1..Q5_8, a grid to one variable per row).
    """

    qid: str
    variable_names: list[str] = field(default_factory=list)
    match_method: Literal["exact", "fuzzy", "llm", "human"] = "exact"
    confidence: float = 1.0
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionVariableLink:
        return cls(**raw)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> QuestionVariableLink:
        return cls.from_dict(json.loads(payload))


@dataclass
class SurveyDefinition:
    """The full designed instrument for one study."""

    study_id: str
    title: str = ""
    source: Literal["extracted", "authored"] = "authored"
    status: Literal["draft", "in_review", "approved", "published"] = "draft"
    version: int = 1
    blocks: list[Block] = field(default_factory=list)
    flow: list[FlowNode] = field(default_factory=list)
    quotas: list[QuotaSheet] = field(default_factory=list)
    embedded_data: list[EmbeddedField] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """(Re)build the qid -> Question lookup from the current question list.

        With duplicate qids this is inherently lossy ("last one wins"), which is
        exactly the silent-corruption bug the review flagged. The index is only a
        lookup convenience — `validate()` is what surfaces the duplicate — but any
        mutation that can change qids (a rename, an add/remove) must call this so a
        later `__getitem__`/`__contains__` in the same request addresses the right
        question rather than a stale mapping built in `__post_init__`.
        """
        self._index = {q.qid: q for q in self.questions}

    def __getitem__(self, qid: str) -> Question:
        return self._index[qid]

    def __contains__(self, qid: str) -> bool:
        return qid in self._index

    def question_order(self) -> list[str]:
        """qids in the order their blocks (and questions within) present them.

        Questions defined but not referenced by any block are appended in list
        order so validation still sees them.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for block in self.blocks:
            for qid in block.question_ids:
                if qid not in seen:
                    ordered.append(qid)
                    seen.add(qid)
        for q in self.questions:
            if q.qid not in seen:
                ordered.append(q.qid)
                seen.add(q.qid)
        return ordered

    def validate(self) -> list[str]:
        """Human-readable problems; empty list means valid."""
        problems: list[str] = []

        # Duplicate qids across all questions.
        seen_qids: set[str] = set()
        for q in self.questions:
            if q.qid in seen_qids:
                problems.append(f"duplicate qid: {q.qid}")
            seen_qids.add(q.qid)

        # Duplicate option codes within one question.
        for q in self.questions:
            for group_name, group in (
                ("options", q.options),
                ("rows", q.rows or []),
                ("cols", q.cols or []),
            ):
                codes: set[int] = set()
                for opt in group:
                    if opt.code in codes:
                        problems.append(
                            f"question {q.qid}: duplicate option code {opt.code} in {group_name}"
                        )
                    codes.add(opt.code)

        # Grids must have non-empty rows.
        for q in self.questions:
            if q.type == QuestionType.GRID_SINGLE and not q.rows:
                problems.append(f"question {q.qid}: grid_single requires non-empty rows")

        # Referenced qids in routing must resolve; forward references are illegal.
        order = self.question_order()
        position = {qid: i for i, qid in enumerate(order)}
        for q in self.questions:
            self_pos = position.get(q.qid)
            for kind, expr in (("show_if", q.show_if), ("terminate_if", q.terminate_if)):
                if expr is None:
                    continue
                for ref in sorted(expr.referenced_qids()):
                    if ref not in seen_qids:
                        problems.append(
                            f"question {q.qid}: {kind} references unknown qid {ref}"
                        )
                        continue
                    if ref == q.qid:
                        # A question conditioning on its own answer is fine
                        # (e.g. terminate_if age < 18 on the age question).
                        continue
                    if self_pos is not None and position.get(ref, -1) > self_pos:
                        problems.append(
                            f"question {q.qid}: {kind} forward-references {ref} "
                            "(routing may only reference already-answered questions)"
                        )

        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "version": self.version,
            "blocks": [b.to_dict() for b in self.blocks],
            "flow": [f.to_dict() for f in self.flow],
            "quotas": [q.to_dict() for q in self.quotas],
            "embedded_data": [e.to_dict() for e in self.embedded_data],
            "questions": [q.to_dict() for q in self.questions],
            "metadata": self.metadata,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SurveyDefinition:
        return cls(
            study_id=raw["study_id"],
            title=raw.get("title", ""),
            source=raw.get("source", "authored"),
            status=raw.get("status", "draft"),
            version=raw.get("version", 1),
            blocks=[Block.from_dict(b) for b in raw.get("blocks", [])],
            flow=[FlowNode.from_dict(f) for f in raw.get("flow", [])],
            quotas=[QuotaSheet.from_dict(q) for q in raw.get("quotas", [])],
            embedded_data=[EmbeddedField.from_dict(e) for e in raw.get("embedded_data", [])],
            questions=[Question.from_dict(q) for q in raw.get("questions", [])],
            metadata=raw.get("metadata", {}),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, payload: str) -> SurveyDefinition:
        return cls.from_dict(json.loads(payload))
