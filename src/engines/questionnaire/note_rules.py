"""The scripter's notes, applied: who is asked, exclusive and anchored answers,
screen-outs and ranges — read the way an analyst reads them.

A programming document writes most of its logic as sentences in notes, not as
bracketed tags: "Ask if selected punch 1-2 at S5d", "ASK QS5, QS5a … in sequence
if SERVICETYPE = 1", "Show module if S5d=1-2", "Make punch 99 exclusive",
"Anchor punch 98 ‘Other’ and punch 99 at the bottom", "Must select at least
punch 1 or 4 to continue, otherwise TERMINATE", "Acceptable range: 1-120. If
less than 18, terminate." Measured on the Charter tracker against a careful
reading: 52 questions had no base and 28 lost an exclusive or anchored answer,
because none of these sentences was read.

Runs once over the finished survey, after every question exists, so a
condition can be checked against the questions and codes it names
(conditions.compile_condition). Nothing already set is overwritten; a sentence
that does not compile is left alone, never guessed.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, QuestionType, SurveyDefinition
from src.engines.questionnaire.conditions import compile_condition
from src.engines.questionnaire.note_lines import note_lines, sentences

_PN = re.compile(r"^\s*\[(?:PN|PROG(?:RAMMER)? NOTE|SCRIPTER NOTE)\s*:\s*(.*?)\]?\s*$", re.I)
_CODES = r"(\d{1,3}(?:\s*(?:-|–|,|&|\band\b|\bor\b)\s*\d{1,3})*)"
_ASK_IF = re.compile(
    r"^\s*(?:(?:route|pn)\s*:\s*)?(?:(?:ask(?:ed)?|show)\s+(?:only\s+)?(?:if|is)\s*:?|"
    r"base\s*:|filter\s*[:–—-]?|if\b)\s*(?P<rule>.+)$", re.I)
_BLOCK_ASK = re.compile(
    r"^\s*ask\s+(?P<qids>(?:Q?[A-Z]{1,4}\d+[a-z]?(?:\s*(?:,|\band\b))*\s*)+)(?:in\s+sequence\s+)?"
    r"if\s+(?P<rule>.+?)(?:\.\s*if not, skip\.?)?\s*$", re.I)
# "Ask for provider brands selected at CSN1=1, 2 or 3": the condition after "at".
_ASK_FOR = re.compile(r"^\s*ask\s+(?:only\s+)?for\b.*?\b(?:at|in)\s+(?P<rule>[A-Za-z]\w*\s*(?:!=|=).+)$",  # noqa: E501
                      re.I)
_MODULE = re.compile(r"^\s*show\s+module\s+(?:only\s+)?if\s+(?P<rule>.+)$", re.I)
_EXCLUSIVE = [
    re.compile(rf"\bmake\s+punch(?:es)?\s+{_CODES}\s+exclusive", re.I),
    re.compile(rf"\bcannot\s+combine\s+(?:punch(?:es)?\s+)?{_CODES}\s+with", re.I),
    re.compile(rf"\ballow\s+multiple\s+responses?\s+except\s+(?:punch(?:es)?\s+)?{_CODES}", re.I),
    re.compile(rf"\bdo\s+not\s+allow\s+(?:any\s+other|multi[- ]?(?:select|punch)|"
               rf"selection\s+of\s+any\s+other)[\w\s]*?\bif\s+(?:punch\s+)?{_CODES}"
               rf"\s+(?:is\s+)?selected", re.I),
]
_ANCHOR = re.compile(rf"\banchor(?:ed)?\s+(?:punch(?:es)?\s+)?{_CODES}", re.I)
_QUOTED = re.compile(r"[‘'“\"]([^’'”\"]{2,60})[’'”\"]")
_RANGE = re.compile(r"(?:acceptable\s+range|allow\s+only\s+numeric\s+values|range)\s*:?\s*"
                    r"(\d{1,6})\s*[-–]\s*(\d{1,6})", re.I)
_LESS_THAN_TERM = re.compile(r"\bif\s+(?:less|under|younger)\s+than\s+(\d{1,4}),?\s*terminate", re.I)  # noqa: E501
_MUST_SELECT = re.compile(rf"\bmust\s+select\s+at\s+least\s+(?:punch(?:es)?\s+)?{_CODES}"
                          r"[^.]*?\botherwise\s+terminate", re.I)
_WRITE_IN = re.compile(r"^\s*write\s+(?:in\s+)?(?:a\s+)?response(?:\s+here)?\s*$", re.I)
_NUMBER_ENTRY = re.compile(r"\bwhole\s+number|\bnumeric\b|\bacceptable\s+range\b|\bnumber\b", re.I)


def _codes(text: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*(?:,|&|\band\b|\bor\b)\s*", text):
        lo, _, hi = part.replace("–", "-").partition("-")
        if lo.strip().isdigit():
            low, high = int(lo), int(hi) if hi.strip().isdigit() else int(lo)
            out += range(low, high + 1) if 0 <= high - low <= 30 else [low, high]
    return out


def _notes(question) -> list[str]:
    """Every note sentence on a question, however the document marks notes."""
    return sentences(note_lines(question))


def _tail_notes(question) -> list[str]:
    """The note lines that END a question's text (they sit above the next one)."""
    if question is None:
        return []
    lines = [ln for ln in (question.source_text or "").splitlines() if ln.strip()]
    tail: list[str] = []
    for line in reversed(lines):
        found = _line_note(line)
        if not found:
            break
        tail.insert(0, found)
    return sentences(tail)


def _lead_notes(question) -> list[str]:
    """The note lines before a question's wording (carried down by the splitter)."""
    lead: list[str] = []
    for line in (question.source_text or "").splitlines():
        if not line.strip():
            continue
        found = _line_note(line)
        if not found:
            break
        lead.append(found)
    return sentences(lead)


def _line_note(line: str) -> str | None:
    """A source line's note, or None if the line is wording or an answer."""
    from types import SimpleNamespace

    found = note_lines(SimpleNamespace(source_text=line))
    return found[0] if found else None


def rule_words(rule: str, host: str) -> str:
    """A note's condition in the grammar conditions.compile_condition reads.

    "selected punch 1-2 at S5d (past 6 months)" -> "S5d = 1, 2"; "punch 1, 2 …
    or 11 selected at S6" -> "S6 = 1, 2, …, 11"; "S5a=9 AND S5d≠1 or 2" ->
    "S5a = 9 and S5d != 1, 2"; "C1=1-9 or 98" -> "C1 = 1, …, 9, 98"; "punch 1 is
    not selected at C7" -> "C7 != 1"; "punch 3 … selected at QETHNICITY".
    """
    text = re.sub(r"\b(\w+)\s+(?:not\s+in)\s*\(([^)]*)\)", r"\1 != \2", rule)
    text = re.sub(r"\b(\w+)\s+in\s*\(([^)]*)\)", r"\1 = \2", text)
    text = re.sub(r"\b(\w+)\s+includes\s+", r"\1 = ", text, flags=re.I)
    text = re.sub(r"\bselected\s+(\d[\d\s,-]*)\s+at\s+(\w+)", r"\2 = \1", text, flags=re.I)
    text = re.sub(r"\b([A-Za-z]{1,3})\.(?=\d)", r"\1", text)  # "Q.4" is Q4
    text = re.sub(r"(\d)\s*/\s*(?=\d)", r"\1, ", text)        # "=1/2"
    text = re.sub(r"\([^)]*\)", "", text).strip().rstrip(".").replace("≠", "!=")
    text = re.sub(r"[“”\"][^“”\"]*[“”\"]", "", text)

    def punch(m: re.Match) -> str:
        neg = "not" in (m.group(0) or "").lower()
        return f"{m.group('q')} {'!=' if neg else '='} {', '.join(map(str, _codes(m.group('c'))))}"

    text = re.sub(rf"(?:selected\s+)?punch(?:es)?\s+(?P<c>{_CODES})\s+(?:is\s+)?(?:not\s+)?"
                  rf"(?:selected\s+)?(?:at|in)\s+(?P<q>[A-Za-z]\w*)", punch, text, flags=re.I)
    text = re.sub(rf"\b(?P<q>[A-Za-z]\w*)\s*(?P<op>!=|=)\s*(?P<c>{_CODES})",
                  lambda m: f"{m.group('q')} {m.group('op')} "
                            f"{', '.join(map(str, _codes(m.group('c'))))}", text)
    text = re.sub(r"\bAND\b", "and", text)
    text = re.sub(r"\bOR\b(?=\s+[A-Za-z]\w*\s*!?=)", "or", text)
    return text.strip()


def _compile(rule: str, host: str, survey: SurveyDefinition) -> Expression | None:
    found = _compile_words(rule_words(rule, host), survey)
    if found is None:
        # "selected punches 2, 3 or 6 at S2, pipe in responses selected at S2":
        # the condition ends where another instruction starts.
        for cut in re.finditer(r"[,;]\s+(?=[a-z]+\b(?!\s*\d))|\s+[-–]\s+", rule):
            if found := _compile_words(rule_words(rule[: cut.start()], host), survey):
                break
    if found is None:
        # "classified as set top box (punch 1 or 3 at S6a)": the rule is the
        # bracketed part, the rest is its name.
        for inner in re.findall(r"\(([^)]*)\)", rule):
            if found := _compile_words(rule_words(inner, host), survey):
                break
    return found


def _compile_words(words: str, survey: SurveyDefinition) -> Expression | None:
    try:
        return compile_condition(words, survey)
    except ValueError:
        # "QS5" where the questions are labelled "S5": the document's own
        # prefix habit, not a missing question.
        fixed = re.sub(r"\bQ(?=[A-Z]{1,3}\d)", "", words)
        try:
            return compile_condition(fixed, survey) if fixed != words else None
        except ValueError:
            return None


def _group_conditions(survey: SurveyDefinition) -> dict[str, list[dict]]:
    """Variables the notes define ("SERVICETYPE: INTERNET IF S2=1 …"), each
    band's rule in order — for notes that test "SERVICETYPE = 1"."""
    from src.engines.questionnaire.reading_questions import analyst_reading

    out: dict[str, list[dict]] = {}
    _GROUP_LABELS.clear()
    for var in analyst_reading(survey)["variables"]:
        if var["status"] == "ready":
            out[var["name"].upper()] = [b["condition"] for b in var["bands"]]
            for k, b in enumerate(var["bands"]):
                label = re.sub(r"[^a-z]", "", b["label"].lower())
                if len(label) >= 3:
                    _GROUP_LABELS[(var["name"].upper(), k)] = label
    return out


def _in_order(short: str, long: str) -> bool:
    letters = iter(long)
    return all(ch in letters for ch in short)


def _resolve_groups(rule: str, groups: dict[str, list[dict]]) -> Expression | None:
    """A note testing a defined variable: "SERVICETYPE = 1" (codes by listing
    order) or "INTERNETINSTALLATION=Self-Install" (the group's own label).
    "MOBILESERVICEACTIVATION" names the MOBILEACTIVATION variable the document
    defined — a near-identical name is read as that one, as a scripter would."""
    m = re.match(r"\s*([A-Za-z][A-Za-z0-9_]{3,40})\s*=\s*([^.(]+?)\s*(?:\(|for\b|\.|$)", rule)
    if not m:
        return None
    name = m.group(1).upper()
    if name not in groups:
        # Every letter of the defined name, in order, inside the one written
        # (or the reverse) — and only one such variable.
        near = [g for g in groups if len(g) >= 6 and (_in_order(g, name) or _in_order(name, g))]
        if len(near) != 1:
            return None
        name = near[0]
    bands, value = groups[name], m.group(2).strip()
    if value.isdigit():
        n = int(value)
        return Expression.from_dict(bands[n - 1]) if 0 < n <= len(bands) and bands[n - 1] else None
    want = re.sub(r"[^a-z]", "", value.lower())
    for k, b in enumerate(bands):
        if b and _GROUP_LABELS.get((name, k)) == want:
            return Expression.from_dict(b)
    return None


def _group_named(rule: str, groups: dict[str, list[dict]]) -> Expression | None:
    """"Show module only if respondent has mobile service": the group a defined
    variable calls MOBILE — when exactly one group of one variable is named."""
    said = re.sub(r"[^a-z]", "", rule.lower())
    hits = [(name, k) for name, bands in groups.items() for k, b in enumerate(bands) if b
            and (label := _GROUP_LABELS.get((name, k))) and label in said]
    if len(hits) != 1:
        return None
    name, k = hits[0]
    return Expression.from_dict(groups[name][k])


_GROUP_LABELS: dict[tuple[str, int], str] = {}


def apply(survey: SurveyDefinition) -> list[dict]:
    """Apply what the notes state; returns one record per setting made."""
    made: list[dict] = []
    by_upper = {q.qid.upper(): q for q in survey.questions}
    groups = _group_conditions(survey)
    module_base: Expression | None = None
    # A question's OWN "Ask if …" wins over a block or module rule that covers
    # it: S6a's "Ask if punch 1…11 at S6" is narrower than "ASK QS6… if
    # SERVICETYPE = 2", and the block note is written first in the document.
    above = ((survey.metadata or {}).get("document_profile") or {}).get(
        "routing_position") == "above"
    ordered = list(survey.questions)
    earlier: set[str] = set()
    for index, q in enumerate(ordered):
        # Where routing is written ABOVE each question, what ends the previous
        # question and what opens this one govern this one; below, its own notes.
        own = ([*_tail_notes(ordered[index - 1] if index else None), *_lead_notes(q)]
               if above else _notes(q))
        for note in own:
            m = _ASK_IF.match(note) or _ASK_FOR.match(note)
            if m and not _BLOCK_ASK.match(note) and q.show_if is None \
                    and not q.qid.startswith(("INFO", "INTRO", "END")):
                expr = (_compile(m.group("rule"), q.qid, survey)
                        or _resolve_groups(m.group("rule"), groups))
                # Who is asked depends only on what was already answered: a base
                # naming this question or a later one belongs to another question.
                if expr and {x.upper() for x in expr.referenced_qids()} <= earlier:
                    expr.verbatim_text = expr.verbatim_text or note
                    q.show_if = expr
                    made.append({"qid": q.qid, "set": "show_if", "said": note, "how": "note"})
        earlier.add(q.qid.upper())

    def set_base(q, expr, said, how):
        if (expr is not None and q.show_if is None
                and not q.qid.startswith(("INFO", "INTRO", "END"))):
            expr.verbatim_text = expr.verbatim_text or said
            q.show_if = expr
            made.append({"qid": q.qid, "set": "show_if", "said": said, "how": how})

    for q in survey.questions:
        notes = _notes(q)
        if q.qid.startswith(("INFO_", "INTRO")):
            module_base = None
            for note in notes:
                if m := _MODULE.match(note):
                    module_base = _compile(m.group("rule"), q.qid, survey)
                    named = re.search(r"\b(?:at|from)\s+([A-Za-z]{1,4}\d+[a-z]?)\b", m.group("rule"))  # noqa: E501
                    if module_base is None:
                        module_base = _group_named(m.group("rule"), groups)
                    if module_base is None and named and (src := by_upper.get(named.group(1).upper())):  # noqa: E501
                        # "Show module only if respondent has Internet service with
                        # allocated brand at S5a": whoever was asked S5a.
                        module_base = (Expression.from_dict(src.show_if.to_dict())
                                       if src.show_if is not None else None)
            continue
        for note in notes:
            if m := _BLOCK_ASK.match(note):
                rule = m.group("rule")
                expr = _resolve_groups(rule, groups) or _compile(rule, q.qid, survey)
                for name in re.findall(r"Q?[A-Z]{1,4}\d+[a-z]?", m.group("qids"), re.I):
                    target = (by_upper.get(name.upper())
                              or by_upper.get(re.sub(r"^Q", "", name.upper())))
                    if target is not None and expr is not None:
                        set_base(target, Expression.from_dict(expr.to_dict()), note, "block")
        if module_base is not None:
            set_base(q, Expression.from_dict(module_base.to_dict()), "module screen", "module")
        _answers(q, notes, made)
        _limits(q, notes, made)
    return made


def _answers(q, notes: list[str], made: list[dict]) -> None:
    """Exclusive and anchored answers, by code or by the label quoted."""
    by_code = {o.code: o for o in q.options or []}
    by_label = {o.label.strip().lower(): o for o in q.options or []}
    for note in notes:
        for pattern in _EXCLUSIVE:
            for m in pattern.finditer(note):
                for code in _codes(m.group(1)):
                    if (o := by_code.get(code)) is not None and not o.exclusive:
                        o.exclusive = True
                        made.append({"qid": q.qid, "set": f"exclusive {code}", "said": note})
        if re.search(r"\bcannot\s+(?:be\s+)?combine", note, re.I):
            for label in _QUOTED.findall(note):
                if (o := by_label.get(label.strip().lower())) is not None and not o.exclusive:
                    o.exclusive = True
                    made.append({"qid": q.qid, "set": f"exclusive {o.code}", "said": note})
        anchored_part = re.split(r"\banchor(?:ed)?\b", note, maxsplit=1, flags=re.I)
        tail = anchored_part[1] if len(anchored_part) > 1 else ""
        # "Anchor punch 98 ‘Other’ and punch 99 at the bottom": a quoted label
        # sits between the codes, so every "punch N" after the word counts.
        tail_codes = [int(c) for c in re.findall(r"\bpunch(?:es)?\s+(\d{1,3})", tail, re.I)]
        for code in tail_codes:
            if (o := by_code.get(code)) is not None and not o.anchored:
                o.anchored = True
                made.append({"qid": q.qid, "set": f"anchored {code}", "said": note})
        for m in _ANCHOR.finditer(note):
            for code in _codes(m.group(1)):
                if (o := by_code.get(code)) is not None and not o.anchored:
                    o.anchored = True
                    made.append({"qid": q.qid, "set": f"anchored {code}", "said": note})
        if re.search(r"\banchor", note, re.I):
            for label in _QUOTED.findall(note):
                if (o := by_label.get(label.strip().lower())) is not None and not o.anchored:
                    o.anchored = True


def _limits(q, notes: list[str], made: list[dict]) -> None:
    """Typed entry, ranges and screen-outs the notes state."""
    write_in = [o for o in q.options or [] if _WRITE_IN.match(o.label)]
    text = " ".join(notes)
    # The question's own box: "WRITE IN RESPONSE", or a bracketed box line the
    # card reader wrote ("[ZIP code: _ _ _ _ _]"). Underscores after an answer
    # ("98. Other, please specify: ____") are that answer's specify box.
    boxed = re.search(r"^\s*(?:\[?\s*write\s+(?:in\s+)?(?:a\s+)?response(?:\s+here)?\s*\]?"
                      r"|\[[^\]\n]*_\s*_[^\]\n]*\])\s*$",
                      q.source_text or "", re.I | re.M)
    if boxed and not write_in:
        # An answer beside the box ("99 Prefer not to answer") closes the box.
        for o in q.options or []:
            o.exclusive = True
        if q.type not in (QuestionType.NUMERIC, QuestionType.OPEN_TEXT):
            q.type = QuestionType.NUMERIC if _NUMBER_ENTRY.search(text) else QuestionType.OPEN_TEXT
            made.append({"qid": q.qid, "set": f"type {q.type.value}", "said": boxed.group(0).strip()})  # noqa: E501
    if boxed and q.type is QuestionType.OPEN_TEXT and re.search(r"\bnumeric\b|\bdigits?\b", text, re.I):  # noqa: E501
        q.type = QuestionType.NUMERIC  # "Numeric field with 5 digits"
    if q.type in (QuestionType.SINGLE, QuestionType.NUMERIC):
        # "Allow one response" on a single or a number says nothing a single
        # does not already mean; as a limit it read as a select-max of 1.
        q.validation = {k: v for k, v in (q.validation or {}).items()
                        if k not in ("max_select", "min_select")} or q.validation
    if q.type is QuestionType.OPEN_TEXT and re.search(r"\benforce\s+response\b", text, re.I):
        q.required = True
    source = q.source_text or ""
    if (q.type is QuestionType.SINGLE and re.search(r"select\s+only\s+one", source, re.I)
            and re.search(r"select\s+all\s+that\s+apply", source, re.I)):
        # Two wordings, one per condition ("[IF 1 ENTERED AT S7a]" … "Select only
        # one." / "[IF 2 OR HIGHER]" … "Select all that apply."): the variable
        # must hold the larger.
        q.type = QuestionType.MULTI
        made.append({"qid": q.qid, "set": "type multi", "said": "two wordings: one / all that apply"})  # noqa: E501
    if write_in:
        # "WRITE IN RESPONSE" is the box, not an answer; what remains (98 I don't
        # know, 99 Prefer not to answer) are answers that close the box.
        q.options = [o for o in q.options if o not in write_in]
        for o in q.options:
            o.exclusive = True
        q.type = QuestionType.NUMERIC if _NUMBER_ENTRY.search(text) else QuestionType.OPEN_TEXT
        made.append({"qid": q.qid, "set": f"type {q.type.value}", "said": write_in[0].label})
    if (m := _RANGE.search(text)) and q.type is QuestionType.NUMERIC:
        q.validation = {**(q.validation or {}), "min": int(m.group(1)), "max": int(m.group(2))}
        made.append({"qid": q.qid, "set": "range", "said": m.group(0)})
    if q.terminate_if is None:
        if (m := _LESS_THAN_TERM.search(text)) and q.type is QuestionType.NUMERIC:
            q.terminate_if = Expression(op="lt", qid=q.qid, value=int(m.group(1)),
                                        verbatim_text=m.group(0))
        elif m := _MUST_SELECT.search(text):
            q.terminate_if = Expression(op="not", children=[
                Expression(op="in", qid=q.qid, values=_codes(m.group(1)))],
                verbatim_text=m.group(0))
        if q.terminate_if is not None:
            made.append({"qid": q.qid, "set": "terminate_if", "said": q.terminate_if.verbatim_text})
