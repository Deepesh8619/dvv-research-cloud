"""A question asked once per category, and the brand lists a document names once.

    Q6. Now, for each of these types of skincare products that you use, which
        brands have you used in the last 6 months?  [SHOW AS CAROUSEL]
        |           | Cleansers | Moisturizers ... | Sunscreens ... | ...
        [PN: DISPLAY CATEGORY IF]     | Q3.1=1-4      | Q3.2=1-4 | Q3.3=1-4 | ...
        [PN: CORE BRANDS TO DISPLAY]  | ALL 10 BRANDS | ... | ALL BRANDS EXCEPT RHODE | ...
        [PN: ... ONLY DISPLAY THOSE AWARE FROM Q2a FOR EACH CATEGORY]

That is six multi-select questions — one per category, each with its own
condition and its own brand list — written as one question over two tables.
Read as a grid it had no rows and no columns, and fielded blank.

The brands come from a list the document names once and uses for several
questions:

    PROGRAMMING: FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF 10 BRANDS BELOW ONLY
    Bubble / CeraVe / Neutrogena / ...

Both are captured from the question's own tables while its chunk is in hand,
and applied once the whole survey exists: the list covers questions in other
chunks, and the brand codes must be the SAME codes as the awareness question the
mask narrows by — a brand coded 3 in Q2a and 7 here matches nothing.

A condition the document wrote incompletely ("OR 6 OR 7=1-4", its question and
first rows missing) is read as far as it goes and flagged: which respondents
see a category is not something to guess.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from src.core.survey_definition import (
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.loops import _replace
from src.engines.questionnaire.prose_directives import is_terminal_option

# "FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF 10 BRANDS BELOW ONLY"
_STUDY_LIST = re.compile(
    r"FOR\s+(?P<first>[A-Z]+\d+[A-Za-z]?)\s*[-–]\s*(?P<last>[A-Z]+\d+[A-Za-z]?)"
    r".{0,40}?\b(?:SET|LIST)\s+OF\s+(?P<n>\d+)\s+(?P<kind>BRANDS?|STATEMENTS?|STORES?|PRODUCTS?)",
    re.I | re.S,
)
_DISPLAY_IF = re.compile(r"DISPLAY\s+(?:CATEGORY|COLUMN|ITEM)\s+IF", re.I)
_BRANDS_ROW = re.compile(r"BRANDS\s+TO\s+DISPLAY|TO\s+DISPLAY\s+BASED\s+ON", re.I)
_CELL_CONDITION = re.compile(r"=\s*(?P<lo>\d+)\s*(?:[-–]\s*(?P<hi>\d+))?\s*$")
_CELL_QID = re.compile(r"\b(?P<qid>[A-Z][A-Za-z]*\d+[A-Za-z]?)\.(?P<row>\d+)")
_ONLY_SHOW = re.compile(r"^\s*ONLY\s+SHOW\b", re.I)
_EXCEPT = re.compile(r"\bEXCEPT\b(?P<rest>.*)$", re.I | re.S)


def _cell_text(cell: str) -> str:
    return " ".join((cell or "").split())


# ---------------------------------------------------------------- capture

def capture(question: Question, tables: list[list[list[str]]]) -> None:
    """Record what this question's tables state, for `apply` to use later."""
    validation = dict(question.validation or {})
    for rows in tables:
        for row in rows:
            for cell in row:
                found = _STUDY_LIST.search(cell or "")
                if found:
                    after = cell[found.end():]
                    items = [x.strip(" /:") for x in re.split(r"\s*/\s*|\n", after)]
                    items = [x for x in items if x and not x.upper().startswith(("ONLY", "BELOW"))]
                    if items:
                        validation.setdefault("study_lists", []).append({
                            "first": found.group("first"), "last": found.group("last"),
                            "kind": found.group("kind").lower().rstrip("s"),
                            "items": items[: int(found.group("n"))],
                            "verbatim": _cell_text(cell[: found.end()]),
                        })
    carousel = _carousel_spec(tables)
    if carousel:
        validation["carousel"] = carousel
    question.validation = validation


def _carousel_spec(tables: list[list[list[str]]]) -> dict[str, Any] | None:
    header: list[str] | None = None
    conditions: list[str] | None = None
    brands: list[str] | None = None
    for rows in tables:
        for row in rows:
            first = _cell_text(row[0]) if row else ""
            rest = [c for c in row[1:]]
            if not first and len([c for c in rest if c.strip()]) >= 2 and header is None:
                header = [_cell_text(c) for c in rest]
            elif _DISPLAY_IF.search(first):
                conditions = [_cell_text(c) for c in rest]
            elif _BRANDS_ROW.search(first):
                brands = list(rest)
    if not header or not conditions:
        return None
    n = len(header)
    return {
        "categories": [
            {
                "label": header[i],
                "condition": conditions[i] if i < len(conditions) else "",
                "brands": (brands[i] if brands and i < len(brands) else "ALL"),
            }
            for i in range(n)
            if header[i]
        ]
    }


# ---------------------------------------------------------------- apply

def apply(survey: SurveyDefinition) -> dict[str, Any]:
    """Resolve named study lists, then expand every captured carousel."""
    lists = _study_lists(survey)
    if lists:
        survey.metadata["study_lists"] = lists
    expanded = []
    for template in list(survey.questions):
        spec = (template.validation or {}).get("carousel")
        if spec:
            parts = _expand(survey, template, spec, lists)
            if parts:
                _replace(survey, template, parts)
                expanded.append({"qid": template.qid, "into": [q.qid for q in parts]})
    if expanded:
        survey.metadata["carousels"] = expanded
        survey._rebuild_index()
    return {"lists": lists, "carousels": expanded}


def _study_lists(survey: SurveyDefinition) -> list[dict]:
    seen, out = set(), []
    for q in survey.questions:
        for item in (q.validation or {}).get("study_lists") or []:
            key = (item["first"], item["last"], tuple(item["items"]))
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


_QNUM = re.compile(r"^(?P<prefix>[A-Za-z]+)(?P<n>\d+)")


def _qnum(qid: str) -> tuple[str, int] | None:
    m = _QNUM.match(qid.split("_")[0])
    return (m.group("prefix").upper(), int(m.group("n"))) if m else None


def list_for(survey: SurveyDefinition, qid: str, lists: list[dict] | None = None) -> dict | None:
    """The named study list whose range ("FOR Q6-Q10") covers this question.

    By question NUMBER, not by position: a range still covers Q8 when Q6 has
    been expanded into Q6_1..Q6_6, or when the question it ends on was renamed.
    """
    here = _qnum(qid)
    if here is None:
        return None
    for item in lists if lists is not None else survey.metadata.get("study_lists") or []:
        first, last = _qnum(item["first"]), _qnum(item["last"])
        if first and last and first[0] == here[0] == last[0] and first[1] <= here[1] <= last[1]:
            return item
    return None


def coded(survey: SurveyDefinition, names: list[str], near: Question | None = None,
          order: list[str] | None = None) -> list[Option]:
    """Options for these names, coded the way the script will code them.

    A list the document names once ("FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF
    10 BRANDS BELOW") is coded by its own order, 1..N — that is what the
    client's script did (Bubble: CeraVe 2, Neutrogena 3), and what earlier
    waves of a tracker were coded as. Masks against an awareness list coded
    differently are matched by brand name (`mask_code_map`). Without such a
    list, the awareness question's codes are used.
    """
    if order:
        position = {name.strip().casefold(): i for i, name in enumerate(order, start=1)}
        spare = len(order) + 1
        out = []
        for name in names:
            code = position.get(name.strip().casefold())
            if code is None:
                code, spare = spare, spare + 1
            out.append(Option(code=code, label=name.strip()))
        return out
    source = _brand_source(survey, near)
    by_label = {o.label.strip().casefold(): o for o in (source.options if source else [])}
    used = {o.code for o in by_label.values()}
    out, spare = [], (max(used) + 1 if used else 1)
    for name in names:
        existing = by_label.get(name.strip().casefold())
        if existing:
            out.append(Option(code=existing.code, label=existing.label))
        else:
            out.append(Option(code=spare, label=name.strip()))
            spare += 1
    return out


def _brand_source(survey: SurveyDefinition, near: Question | None) -> Question | None:
    """The mask's source if there is one, else the fullest earlier brand list."""
    if near is not None:
        for mask in ((near.validation or {}).get("techniques") or {}).get("masks") or []:
            wanted = str(mask.get("source_qid") or "").upper()
            for q in survey.questions:
                if q.qid.upper() == wanted and q.options:
                    return q
    brandish = [q for q in survey.questions if "brand" in (q.text or "").lower() and q.options]
    return max(brandish, key=lambda q: len(q.options), default=None)


def _expand(
    survey: SurveyDefinition, template: Question, spec: dict, lists: list[dict]
) -> list[Question]:
    core = list_for(survey, template.qid, lists)
    core_names = core["items"] if core else []
    extras = [
        _written_code(replace(o)) for o in template.options
        if is_terminal_option(o.label) or o.open_other or re.search(r"\(\s*specify", o.label, re.I)
    ]
    # "[PN: ... ONLY DISPLAY THOSE AWARE FROM Q2a FOR EACH CATEGORY]": each
    # category shows only the brands this respondent has heard of.
    aware = _AWARE_FROM.search(template.source_text or "")
    carried_qid = next(
        (m.group("qid") for c in spec["categories"] if (m := _CELL_QID.search(c["condition"]))),
        None,
    )
    parts: list[Question] = []
    for index, category in enumerate(spec["categories"], start=1):
        names = _brands_for(category["brands"], core_names)
        if not names:
            continue
        condition, complete = _condition(category["condition"], carried_qid)
        validation = dict(template.validation or {})
        validation.pop("carousel", None)
        if aware and not (validation.get("techniques") or {}).get("masks"):
            techniques = dict(validation.get("techniques") or {})
            techniques["masks"] = [{"source_qid": aware.group(1), "mode": "selected",
                                    "exclude_qid": None, "verbatim": aware.group(0)}]
            validation["techniques"] = techniques
        validation["loop"] = {"of": template.qid, "index": index, "label": category["label"],
                              "randomised_order": True}
        flags = [f for f in template.flags if f not in (schema.FLAG_EMPTY_OPTIONS,)]
        evidence = {
            **template.extraction_evidence,
            "options": f"{category['brands'].split(chr(10))[0].strip() or 'ALL'} of "
                       + (f"the {core['kind']} list for {core['first']}-{core['last']}"
                          if core else "the brand list"),
        }
        if condition is not None:
            evidence["show_if"] = category["condition"]
        if not complete:
            flags.append(schema.FLAG_AMBIGUOUS_ROUTING)
            evidence["show_if"] = (
                f"'{category['condition']}' names no question for its first rows — "
                "confirm which respondents see this category"
            )
        options = coded(survey, names, template, order=core_names or None)
        taken = {o.code for o in options}
        options += [o for o in extras if o.code not in taken]
        parts.append(Question(
            qid=f"{template.qid}_{index}",
            text=f"{template.text} ({category['label']})",
            type=QuestionType.MULTI,
            options=options,
            required=template.required,
            randomize_options=True,
            show_if=condition,
            piping_refs=list(template.piping_refs or []),
            validation=validation,
            source_text=template.source_text,
            source_location=template.source_location,
            extraction_method="category_carousel",
            extraction_evidence=evidence,
            flags=flags,
        ))
    return parts


_AWARE_FROM = re.compile(r"\bAWARE\s+(?:FROM|AT|IN)\s+([A-Z][A-Za-z]*\d+[A-Za-z]?)", re.I)
# "98 Another brand…" — and "98Another brand…", as the Bubble document wrote it.
_LEADING_CODE = re.compile(r"^\s*(\d{1,3})\s*(?=[A-Za-z])")


def _written_code(option: Option) -> Option:
    """ "98 Another brand not listed (Specify)" is code 98, not "98 Another brand"."""
    written = _LEADING_CODE.match(option.label or "")
    if written:
        option.code = int(written.group(1))
        option.label = option.label[written.end():].strip()
    option.anchored = True
    return option


def _brands_for(rule: str, core: list[str]) -> list[str]:
    text = rule or ""
    lines = [x.strip(" /") for x in re.split(r"\n|/", text) if x.strip(" /")]
    if _ONLY_SHOW.match(text):
        named = [x for x in lines if not _ONLY_SHOW.match(x)]
        return _match(named, core) if core else named
    excluded = _EXCEPT.search(text)
    if excluded:
        named = re.split(r",|\band\b|/", excluded.group("rest"))
        drop = {x.casefold() for x in _match(named, core)}
        return [c for c in core if c.casefold() not in drop]
    return list(core)


def _match(names: list[str], core: list[str]) -> list[str]:
    """Names as written in the core list, in the order given."""
    out = []
    for name in (n.strip() for n in names):
        if not name:
            continue
        hit = next((c for c in core if c.casefold() == name.casefold()), None)
        out.append(hit or name)
    return out


def _condition(cell: str, carried_qid: str | None) -> tuple[Expression | None, bool]:
    """ "Q3.1=1-4" -> row 1 of Q3 answered 1-4. Returns (condition, complete)."""
    text = _cell_text(cell)
    if not text:
        return None, True
    value = _CELL_CONDITION.search(text)
    if not value:
        return None, False
    lo, hi = int(value.group("lo")), int(value.group("hi") or value.group("lo"))
    head = text[: value.start()]
    named = _CELL_QID.search(head)
    qid = named.group("qid") if named else carried_qid
    rows = [int(r) for r in re.findall(r"\d+", head.replace(qid or "", "", 1) if named else head)]
    if named:
        rows = [int(named.group("row"))] + [
            int(r) for r in re.findall(r"\d+", head[named.end():])
        ]
    if not qid or not rows:
        return None, False
    values = list(range(lo, hi + 1))
    parts = [
        Expression(op="in", qid=qid, row=row, values=values, verbatim_text=text) for row in rows
    ]
    expression = (
        parts[0] if len(parts) == 1
        else Expression(op="or", children=parts, verbatim_text=text)
    )
    return expression, named is not None
