"""Prove, for every possible respondent, which questions can be reached.

A dry run of 1,000 respondents can miss a path that one person in 50,000 takes.
A solver does not sample: it answers "can anyone reach Q7?" for all of them at
once, and when the answer is no, the unsat core says which rules collide.

The encoding mirrors `survey_definition.evaluate` and the field engine's
`is_visible` / `terminating_question` (src/engines/fielding/decisions.py):

* a leaf over a question nobody answered is False, so `not(leaf)` is True;
* a base (show_if) is only judged once every question it names is answered;
* a question is shown when its base holds and no earlier answered question has
  screened the respondent out; a screen-out is checked after each shown question
  and fires once every question it names is answered;
* a condition can only see questions that come BEFORE it in the survey's order.

Out of scope (ponytail: v1 ceiling; masks and option-level show_if only narrow
what a respondent may pick, so ignoring them can only make a question look
reachable, never unreachable): derived variables, masks, option show_if, quotas.
A question whose base depends on a derived variable, a free-text answer or a
MaxDiff is reported as "not provable", never as unreachable.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

import z3

from src.core.survey_definition import (
    GRID_TYPES,
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
    _as_number,
)
from src.engines.questionnaire.derived import has_rule, is_coded_hidden

_OPS = {"eq", "ne", "in", "gt", "lt", "and", "or", "not", "selected"}
_CHECK_MS = 3000  # per question; a timeout is reported as not provable
_SYMBOL = {"eq": "=", "ne": "≠", "gt": ">", "lt": "<"}


class _Unmodelled(Exception):
    """A condition the solver cannot represent faithfully."""


def _eq_number(value: Any) -> Fraction | None:
    """`value` as it would compare equal to a stored code, or None if nothing would."""
    if isinstance(value, bool):
        return Fraction(int(value))
    if isinstance(value, (int, float)) and (isinstance(value, int) or math.isfinite(value)):
        return Fraction(value)
    return None


def _and(items: Any) -> z3.BoolRef:
    items = list(items)
    return z3.And(*items) if items else z3.BoolVal(True)


def _or(items: Any) -> z3.BoolRef:
    items = list(items)
    return z3.Or(*items) if items else z3.BoolVal(False)


def _real(f: Fraction) -> z3.ArithRef:
    return z3.RealVal(f"{f.numerator}/{f.denominator}")


def _codes_text(codes: list[int]) -> str:
    """1, 2, 3 -> '1–3'; 1, 2, 5 -> '1, 2, 5'."""
    codes = sorted(set(codes))
    if len(codes) > 2 and codes[-1] - codes[0] == len(codes) - 1:
        return f"{codes[0]}–{codes[-1]}"
    return ", ".join(str(c) for c in codes)


def render(e: Expression) -> str:
    """A condition in words a researcher would write."""
    where = f"{e.qid} row {e.row}" if e.row is not None else str(e.qid)
    kids = [render(c) for c in e.children or []]
    if e.op in _SYMBOL:
        return f"{where} {_SYMBOL[e.op]} {e.value}"
    if e.op == "in":
        return f"{where} is one of {', '.join(str(v) for v in e.values or [])}"
    if e.op == "selected":
        return f"{where} includes {e.value}"
    if e.op == "not":
        return "not (" + " or ".join(kids) + ")"
    return "(" + (" and " if e.op == "and" else " or ").join(kids) + ")" if len(kids) > 1 \
        else (kids[0] if kids else "always" if e.op == "and" else "never")


class _Encoding:
    def __init__(self, survey: SurveyDefinition) -> None:
        self.survey = survey
        self.order = [q for q in survey.question_order() if q in survey]
        self.pos = {qid: i for i, qid in enumerate(self.order)}
        self.q = {qid: survey[qid] for qid in self.order}
        self.solver = z3.Solver()
        self.solver.set("timeout", _CHECK_MS)
        self.labels: dict[str, tuple[str, str]] = {}  # tracker name -> (kind, qid)
        self.free = 0
        self.not_provable: dict[str, str] = {}
        self.shown: dict[str, z3.BoolRef] = {}
        self.ans: dict[str, Any] = {}
        self.val: dict[str, Any] = {}      # scalar Int, or {row: Int}
        self.bools: dict[str, Any] = {}    # {code: Bool}, or {row: {code: Bool}}
        self.fire: dict[str, z3.BoolRef] = {}
        self.fire_reach: dict[str, int] = {}
        self.term_after: dict[str, z3.BoolRef] = {}  # qid -> screened by the time it was answered
        self.nothing: set[str] = set()      # shown, but nothing to answer
        self.domain_text: dict[str, str] = {}
        self.tainted: dict[str, str] = {}   # base the solver cannot model
        self.opaque = {qid: self._opaque(q) for qid, q in self.q.items()}
        self._declare()
        self._route()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _opaque(q: Question) -> str | None:
        if is_coded_hidden(q) and has_rule(q):
            return "uses a derived variable"
        if q.type in (QuestionType.OPEN_TEXT, QuestionType.MAXDIFF):
            return "tests a free-text or MaxDiff answer, which the solver cannot model"
        return None

    def _track(self, expr: z3.BoolRef, kind: str, qid: str) -> None:
        name = f"t{len(self.labels)}"
        self.labels[name] = (kind, qid)
        self.solver.assert_and_track(expr, z3.Bool(name))

    def _fresh(self) -> z3.BoolRef:
        self.free += 1
        return z3.Bool(f"free{self.free}")

    # -- variables and answer domains ---------------------------------------

    def _declare(self) -> None:
        for qid, q in self.q.items():
            self.shown[qid] = z3.Bool(f"shown_{qid}")
            self._domain(qid, q)

    def _domain(self, qid: str, q: Question) -> None:
        t, s = q.type, z3.Bool(f"shown_{qid}")
        opts = [o.code for o in q.options]
        rows = [r.code for r in q.rows or []]
        cols = [c.code for c in q.cols or []]
        add: list[z3.BoolRef] = []
        text = ""
        answerable = True
        if t == QuestionType.INFO:
            self.ans[qid] = z3.BoolVal(bool(opts))
            if opts:
                self.val[qid] = z3.Int(f"v_{qid}")
                add.append(z3.Or(*[self.val[qid] == c for c in opts]))
                text = f"{qid} only allows {_codes_text(opts)}"
            self._maybe_track(add, qid, text)
            return
        if t in (QuestionType.SINGLE, QuestionType.DROPDOWN):
            answerable = bool(opts)
            if answerable:
                v = self.val[qid] = z3.Int(f"v_{qid}")
                add.append(_or([v == c for c in opts]))
                text = f"{qid} only allows {_codes_text(opts)}"
        elif t in (QuestionType.MULTI, QuestionType.RANK):
            answerable = bool(opts)
            if answerable:
                b = self.bools[qid] = {c: z3.Bool(f"b_{qid}_{c}") for c in opts}
                if t == QuestionType.RANK:
                    add.append(z3.Implies(s, _and(b.values())))
                else:
                    add.append(z3.Implies(s, _or(b.values())))
                for o in q.options:
                    if o.exclusive:
                        add += [z3.Implies(b[o.code], z3.Not(b[c])) for c in opts if c != o.code]
                text = f"{qid} only offers {_codes_text(opts)}"
        elif t == QuestionType.GRID_SINGLE:
            answerable = bool(rows and cols)
            if answerable:
                v = self.val[qid] = {r: z3.Int(f"v_{qid}_{r}") for r in rows}
                add += [_or([x == c for c in cols]) for x in v.values()]
                text = f"{qid} only allows {_codes_text(cols)}"
        elif t == QuestionType.GRID_MULTI:
            answerable = bool(rows and cols)
            if answerable:
                b = self.bools[qid] = {r: {c: z3.Bool(f"b_{qid}_{r}_{c}") for c in cols}
                                       for r in rows}
                for r in rows:
                    add.append(z3.Implies(s, z3.Or(*b[r].values())))
                    for o in q.cols or []:
                        if o.exclusive:
                            add += [z3.Implies(b[r][o.code], z3.Not(b[r][c]))
                                    for c in cols if c != o.code]
                text = f"{qid} only allows {_codes_text(cols)}"
        elif t == QuestionType.NUMERIC:
            validation = q.validation or {}
            lo, hi = _as_number(validation.get("min")), _as_number(validation.get("max"))
            if rows:
                total = _as_number(validation.get("sum"))
                v = self.val[qid] = {r: z3.Int(f"v_{qid}_{r}") for r in rows}
                add += [x >= 0 for x in v.values()]
                if total is not None:
                    add.append(z3.Sum(*v.values()) == int(total))
                    text = f"{qid} shares must add up to {int(total)}"
            else:
                v = self.val[qid] = z3.Int(f"v_{qid}")
                if lo is not None:
                    add.append(v >= math.ceil(lo))
                if hi is not None:
                    add.append(v <= math.floor(hi))
                if lo is not None and hi is not None:
                    text = f"{qid} only allows {math.ceil(lo)}–{math.floor(hi)}"
                elif lo is not None:
                    text = f"{qid} only allows {math.ceil(lo)} or more"
                elif hi is not None:
                    text = f"{qid} only allows {math.floor(hi)} or less"
        elif t in (QuestionType.OPEN_TEXT, QuestionType.MAXDIFF):
            answerable = t == QuestionType.OPEN_TEXT
        if answerable:
            self.ans[qid] = s
            self._maybe_track(add, qid, text)
        else:
            a = self.ans[qid] = z3.Bool(f"ans_{qid}")
            self._track(z3.Not(a), "dom", qid)
            self.nothing.add(qid)

    def _maybe_track(self, add: list[z3.BoolRef], qid: str, text: str) -> None:
        if add:
            self._track(_and(add), "dom", qid)
        if text:
            self.domain_text[qid] = text

    # -- conditions ---------------------------------------------------------

    def _answered(self, qid: str, upto: int) -> z3.BoolRef:
        """Whether `qid` holds an answer at step `upto` (only earlier questions do)."""
        if qid not in self.pos or self.pos[qid] >= upto:
            return z3.BoolVal(False)
        if self.opaque[qid]:
            return z3.Bool(f"opq_{qid}")
        return self.ans[qid]

    def holds(self, e: Expression, upto: int) -> z3.BoolRef:
        """Mirror of `evaluate` over the symbolic answers of questions before `upto`."""
        if e.op not in _OPS:
            raise _Unmodelled(f"uses an unknown rule type ({e.op})")
        if e.op == "and":
            return _and(self.holds(c, upto) for c in e.children or [])
        if e.op == "or":
            return _or(self.holds(c, upto) for c in e.children or [])
        if e.op == "not":
            return z3.Not(_or(self.holds(c, upto) for c in e.children or []))
        if e.qid is None or e.qid not in self.pos or self.pos[e.qid] >= upto:
            return z3.BoolVal(False)
        if self.opaque[e.qid]:
            return self._fresh()
        return z3.And(self.ans[e.qid], self._leaf(e, self.q[e.qid]))

    def _leaf(self, e: Expression, q: Question) -> z3.BoolRef:
        """The test itself, assuming the question was answered."""
        qid, t = q.qid, q.type
        true, false = z3.BoolVal(True), z3.BoolVal(False)
        if qid in self.nothing:
            return false  # nothing can be answered here; the answered flag is False anyway
        v, b = self.val.get(qid), self.bools.get(qid)
        keyed = t in GRID_TYPES or (t == QuestionType.NUMERIC and q.rows)
        if e.row is not None:
            if not keyed:
                return false
            try:
                row = int(e.row)
            except (TypeError, ValueError):
                return false
            if t == QuestionType.GRID_MULTI:
                return self._list_test(e, b.get(row)) if b and row in b else false
            return self._scalar_test(e, v[row]) if v and row in v else false
        if keyed:
            # No row named: the whole {row: answer} map. eq is never equal, ne always is,
            # and in/selected ask whether ANY row holds one of the codes.
            if e.op in ("eq", "gt", "lt"):
                return false
            if e.op == "ne":
                return true
            codes = [e.value] if e.op == "selected" else list(e.values or [])
            num = [n for n in map(_eq_number, codes) if n is not None]
            if t == QuestionType.GRID_MULTI:
                return _or(b[r][int(n)] for r in b for n in num
                           if n.denominator == 1 and int(n) in b[r])
            return _or([x == int(n) for x in v.values() for n in num if n.denominator == 1])
        if t in (QuestionType.MULTI, QuestionType.RANK):
            return self._list_test(e, b)
        if v is None:
            # A comparison on a question whose answers are not modelled (an open
            # text, a list with no codes): say so rather than guess either way.
            raise _Unmodelled(f"tests {qid}, whose answers the solver does not model")
        return self._scalar_test(e, v)

    @staticmethod
    def _scalar_test(e: Expression, v: z3.ArithRef) -> z3.BoolRef:
        if e.op in ("eq", "ne", "selected"):
            n = _eq_number(e.value)
            hit = v == int(n) if n is not None and n.denominator == 1 else z3.BoolVal(False)
            return z3.Not(hit) if e.op == "ne" else hit
        if e.op == "in":
            nums = [n for n in map(_eq_number, e.values or []) if n is not None
                    and n.denominator == 1]
            return _or([v == int(n) for n in nums])
        right = _as_number(e.value)  # not a number: never satisfied
        if right is None:
            return z3.BoolVal(False)
        r = _real(Fraction(right))
        return z3.ToReal(v) > r if e.op == "gt" else z3.ToReal(v) < r

    @staticmethod
    def _list_test(e: Expression, b: dict[int, z3.BoolRef] | None) -> z3.BoolRef:
        """A list answer: eq/selected mean 'ticked', ne 'not ticked'; gt/lt are never met."""
        if not b or e.op in ("gt", "lt"):
            return z3.BoolVal(False)

        def ticked(value: Any) -> z3.BoolRef:
            n = _eq_number(value)
            return b[int(n)] if n is not None and n.denominator == 1 and int(n) in b \
                else z3.BoolVal(False)

        if e.op in ("eq", "selected"):
            return ticked(e.value)
        if e.op == "ne":
            return z3.Not(ticked(e.value))
        return _or(ticked(v) for v in e.values or [])

    # -- the walk -----------------------------------------------------------

    def _route(self) -> None:
        end = len(self.order) + 1
        # Screen-outs: one `fire` flag per rule, switched on at the step its last
        # question is answered. A rule that names an unknown question never fires.
        reach: dict[int, list[str]] = {}
        for qid, q in self.q.items():
            rule = q.terminate_if
            if rule is None:
                continue
            refs = rule.referenced_qids()
            if not refs <= self.pos.keys():
                continue
            blocked = next((r for r in sorted(refs) if self.opaque[r]), None)
            if blocked:
                self.not_provable.setdefault(
                    qid,
                    f"its screen-out {self.opaque[blocked]}; the solver assumed it never fires")
                continue
            try:
                body = self.holds(rule, end)
            except _Unmodelled as err:
                self.not_provable.setdefault(qid, f"its screen-out {err}")
                continue
            f = self.fire[qid] = z3.Bool(f"fire_{qid}")
            self._track(f == z3.And(*[self._answered(r, end) for r in refs], body), "screen", qid)
            reach.setdefault(max((self.pos[r] for r in refs), default=0), []).append(qid)
        screened: z3.BoolRef = z3.BoolVal(False)  # screened out before the current question
        cum: z3.BoolRef = z3.BoolVal(False)
        prev_cum: z3.BoolRef = cum
        previous: Question | None = None
        for k, qid in enumerate(self.order):
            q = self.q[qid]
            cum = z3.Or(cum, *[self.fire[p] for p in reach.get(k, [])])
            if previous is not None and previous.type != QuestionType.INFO:
                screened = z3.Or(screened, z3.And(self.shown[previous.qid], prev_cum))
            prev_cum = cum
            previous = q
            self.term_after[qid] = z3.Bool(f"term_{qid}")
            self.solver.add(self.term_after[qid] == z3.Or(screened, z3.And(self.shown[qid], cum)))
            if q.type == QuestionType.INFO:
                continue
            gate = z3.Not(screened)
            if q.show_if is None:
                self.solver.add(self.shown[qid] == gate)
                continue
            refs = q.show_if.referenced_qids()
            reason = next((self.opaque[r] for r in sorted(refs)
                           if self.opaque.get(r)), None) or next(
                (f"depends on {r}, which is not provable" for r in sorted(refs)
                 if r in self.tainted), None)
            vis = z3.Bool(f"vis_{qid}")
            try:
                if reason:
                    raise _Unmodelled(reason)
                body = z3.And(*[self._answered(r, k) for r in refs], self.holds(q.show_if, k))
                self._track(vis == body, "show", qid)
            except _Unmodelled as err:
                self.tainted[qid] = str(err)
                self.not_provable[qid] = str(err)
            self.solver.add(self.shown[qid] == z3.And(gate, vis))

    # -- answers out of a model ----------------------------------------------

    def example(self, m: z3.ModelRef, upto_qid: str) -> dict[str, Any]:
        out: dict[str, Any] = {}

        def num(x: Any) -> int:
            return m.eval(x, model_completion=True).as_long()

        def on(x: Any) -> bool:
            return z3.is_true(m.eval(x, model_completion=True))

        for qid in self.order[: self.pos[upto_qid] + 1]:
            q = self.q[qid]
            if self.opaque[qid] or not on(self.ans[qid]):
                continue
            v, b = self.val.get(qid), self.bools.get(qid)
            if q.type == QuestionType.GRID_MULTI:
                out[qid] = {str(r): sorted(c for c, x in cs.items() if on(x))
                            for r, cs in b.items()}
            elif isinstance(v, dict):
                out[qid] = {str(r): num(x) for r, x in v.items()}
            elif v is not None:
                out[qid] = num(v)
            elif b is not None:
                out[qid] = sorted(c for c, x in b.items() if on(x))
            elif q.type == QuestionType.OPEN_TEXT:
                out[qid] = "it was fine"
        return out

    def why(self, core: list[Any], qid: str) -> str:
        names = {str(c) for c in core}
        hit = [(kind, q) for n, (kind, q) in self.labels.items() if n in names]
        shows = sorted((q for kind, q in hit if kind == "show"), key=self.pos.get)
        needs = [f"{q} needs {render(self.q[q].show_if)}" for q in shows
                 if self.q[q].show_if is not None]
        screens = [f"{q} screens out anyone with {render(self.q[q].terminate_if)}"
                   for q, kind in ((q, k) for k, q in hit if k == "screen")
                   if self.q[q].terminate_if is not None]
        domains = []
        for kind, q in hit:
            if kind != "dom":
                continue
            domains.append(f"{q} has nothing to answer" if q in self.nothing
                           else self.domain_text.get(q, ""))
        # A question it depends on that nobody can answer (no answer codes) never
        # shows up in the unsat core, since it is simply never answered: name it.
        for q in shows:
            for ref in sorted(self.q[q].show_if.referenced_qids() if self.q[q].show_if else ()):
                text_ = f"{ref} has no answer options, so nobody can answer it"
                if ref in self.nothing and text_ not in domains:
                    domains.append(text_)
        domains = [d for d in domains if d]
        text = "; ".join(needs) or f"{qid} is blocked"
        if domains:
            text += ", but " + " and ".join(domains)
        elif len(needs) == 1 and not screens:
            text += ", which can never be true together"
        if screens:
            text += ("; " if needs else "") + "everyone who gets that far has already been " \
                "screened out: " + "; ".join(screens)
        return text


def prove(survey: SurveyDefinition) -> dict[str, Any]:
    """Which questions can be reached by some respondent, and why not when none can."""
    enc = _Encoding(survey)
    s = enc.solver
    unreachable: list[dict[str, str]] = []
    examples: dict[str, dict[str, Any]] = {}
    not_provable = dict(enc.not_provable)
    for qid in enc.order:
        q = enc.q[qid]
        if q.type == QuestionType.INFO or qid in enc.not_provable:
            continue
        verdict = s.check(enc.shown[qid])
        if verdict == z3.sat:
            examples[qid] = enc.example(s.model(), qid)
        elif verdict == z3.unsat:
            unreachable.append({"qid": qid, "why": enc.why(s.unsat_core(), qid)})
        else:
            not_provable[qid] = "the solver ran out of time on it"

    impossible, always = [], []
    for qid, f in enc.fire.items():
        if enc.solver.check(f) == z3.unsat:
            impossible.append({"qid": qid})
        elif qid in examples and enc.solver.check(
                enc.shown[qid], z3.Not(enc.term_after[qid])) == z3.unsat:
            always.append(qid)
    return {
        "unreachable": unreachable,
        "impossible_screenouts": impossible,
        "always_screened_out": always,
        "not_provable": [{"qid": k, "reason": v} for k, v in not_provable.items()],
        "examples": examples,
    }


def summary(proof: dict[str, Any]) -> str:
    """One plain sentence about the proof, for the certificate and the X-ray panel."""
    gone = proof["unreachable"]
    if gone:
        return "; ".join(f"{u['qid']} can never be shown — {u['why']}" for u in gone)
    if proof["not_provable"]:
        return "Proved except: " + "; ".join(
            f"{n['qid']} ({n['reason']})" for n in proof["not_provable"])
    return "Proved for every possible respondent"
