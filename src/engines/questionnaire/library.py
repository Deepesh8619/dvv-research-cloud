"""Ready-made questionnaire modules and study templates for building from scratch.

A researcher writing a brand tracker does not write "Which of these brands have
you heard of?" from nothing; every agency has its standard wording, scales and
routing for the screener, the awareness funnel, NPS, a satisfaction grid, a
concept test, MaxDiff and Van Westendorp. These are those blocks, written the
standard way:

- scales balanced, labelled at both ends, the same direction throughout;
- "None of these" exclusive and anchored, brand lists randomised;
- the routing that belongs to the block written in (the screener terminates,
  consideration is asked only of people aware of something, the NPS "why"
  gets a follow-up when it is thin);
- question ids that say what they are, renumbered if the survey already has one.

A template is an ordered set of modules for a type of study. Everything a
module needs (category, brands, attributes) is asked once, in plain words.
"""

from __future__ import annotations

import math
from typing import Any

from src.core.survey_definition import Expression, Option, Question, QuestionType

Inputs = dict[str, Any]

FIELDS: dict[str, dict[str, str]] = {
    "organisation": {"label": "Your organisation", "kind": "text",
                     "placeholder": "DVV Research"},
    "purpose": {"label": "What the research is for", "kind": "text",
                "placeholder": "understanding how people choose coffee"},
    "contact": {"label": "Contact for withdrawal and grievances", "kind": "text",
                "placeholder": "privacy@yourfirm.com"},
    "min_age": {"label": "Youngest age allowed", "kind": "number", "placeholder": "18"},
    "category": {"label": "Product category", "kind": "text", "placeholder": "instant coffee"},
    "brands": {"label": "Brands (one per line)", "kind": "lines",
               "placeholder": "Nescafé\nBru\nDavidoff"},
    "subject": {"label": "Brand or company being rated", "kind": "text", "placeholder": "Nescafé"},
    "attributes": {"label": "Things to rate (one per line)", "kind": "lines",
                   "placeholder": "Taste\nPrice\nPackaging"},
    "concept": {"label": "The concept, as respondents will read it", "kind": "long",
                "placeholder": "A ready-to-drink cold coffee in a 200 ml can…"},
    "product": {"label": "Product being priced", "kind": "text",
                "placeholder": "the cold coffee can"},
    "currency": {"label": "Currency", "kind": "text", "placeholder": "₹"},
    "items": {"label": "Items to compare (one per line, 6 to 30)", "kind": "lines",
              "placeholder": "Free delivery\nCash back\n24/7 support"},
    "question": {"label": "The open question", "kind": "long",
                 "placeholder": "Why did you choose this brand?"},
    "specialties": {"label": "Specialties allowed (one per line)", "kind": "lines",
                    "placeholder": "Psychiatrist\nGeneral practitioner"},
}

NONE_CODE, DK_CODE, PNTS_CODE = 98, 99, 99


def _opts(labels: list[str]) -> list[Option]:
    return [Option(code=i, label=label) for i, label in enumerate(labels, start=1)]


def _lines(value: Any) -> list[str]:
    items = value if isinstance(value, list) else str(value or "").splitlines()
    return [s.strip() for s in items if str(s).strip()]


def _brand_list(inputs: Inputs) -> list[Option]:
    return [*_opts(_lines(inputs["brands"])),
            Option(code=NONE_CODE, label="None of these", exclusive=True, anchored=True)]


def _scale5(low: str, high: str) -> list[Option]:
    """Very low, somewhat low, neither, somewhat high, very high: balanced and labelled."""
    lo, hi = low.lower().replace("very ", ""), high.lower().replace("very ", "")
    return _opts([low, f"Somewhat {lo}", f"Neither {hi} nor {lo}", f"Somewhat {hi}", high])


def _consent(i: Inputs) -> list[Question]:
    from src.engines.qc.compliance import notice_questions

    return notice_questions(i["organisation"], i["purpose"], i["contact"],
                            health=bool(i.get("health")))


def _screener(i: Inputs) -> list[Question]:
    floor = int(i.get("min_age") or 18)
    return [
        Question(qid="S_AGE", text="What is your age?", type=QuestionType.NUMERIC,
                 validation={"min": 0, "max": 99},
                 terminate_if=Expression(op="lt", qid="S_AGE", value=floor,
                                         verbatim_text=f"TERMINATE if under {floor}")),
        Question(qid="S_GENDER", text="How do you describe yourself?", type=QuestionType.SINGLE,
                 options=[*_opts(["Man", "Woman", "Non-binary"]),
                          Option(code=PNTS_CODE, label="Prefer not to say", exclusive=True)]),
    ]


def _hcp_screener(i: Inputs) -> list[Question]:
    specs = _lines(i["specialties"])
    other = len(specs) + 1
    return [
        Question(qid="S_SPECIALTY", text="What is your primary medical specialty?",
                 type=QuestionType.SINGLE,
                 options=[*_opts(specs), Option(code=other, label="Other")],
                 terminate_if=Expression(op="eq", qid="S_SPECIALTY", value=other,
                                         verbatim_text="TERMINATE if another specialty")),
        Question(qid="S_YEARS", text="How many years have you been in clinical practice since "
                 "completing your training?", type=QuestionType.NUMERIC,
                 validation={"min": 0, "max": 60},
                 terminate_if=Expression(op="lt", qid="S_YEARS", value=2,
                                         verbatim_text="TERMINATE if under 2 years")),
        Question(qid="S_PATIENTS", text="Roughly how many patients do you personally see in a "
                 "typical month?", type=QuestionType.NUMERIC, validation={"min": 0, "max": 5000}),
    ]


def _aware_only(source: str) -> dict:
    """Show only the brands this person said they know ("None" stays offered)."""
    return {"techniques": {"masks": [{"source_qid": source, "mode": "selected"}]}}


def _awareness(i: Inputs) -> list[Question]:
    cat = i["category"]
    aware = Expression(op="not", children=[Expression(op="selected", qid="AWR_AIDED",
                                                      value=NONE_CODE)])
    return [
        Question(qid="AWR_UNAIDED", text=f"When you think of {cat}, which brands come to mind? "
                 "Please list all you can think of.", type=QuestionType.OPEN_TEXT),
        Question(qid="AWR_AIDED", text=f"Which of these {cat} brands have you heard of?",
                 type=QuestionType.MULTI, options=_brand_list(i), randomize_options=True),
        Question(qid="AWR_CONSIDER", text=f"Which of these would you consider buying next time "
                 f"you buy {cat}?", type=QuestionType.MULTI, options=_brand_list(i),
                 randomize_options=True, show_if=aware, validation=_aware_only("AWR_AIDED")),
        Question(qid="AWR_USED", text="Which of these have you bought in the past 3 months?",
                 type=QuestionType.MULTI, options=_brand_list(i), randomize_options=True,
                 show_if=aware, validation=_aware_only("AWR_AIDED")),
        Question(qid="AWR_PREFER", text=f"And which one is your most preferred brand of {cat}?",
                 type=QuestionType.SINGLE, options=_brand_list(i), randomize_options=True,
                 show_if=aware, validation=_aware_only("AWR_AIDED")),
    ]


def _nps(i: Inputs) -> list[Question]:
    subject = i["subject"]
    scale = [Option(code=n, label=("0 - Not at all likely" if n == 0 else
                                   "10 - Extremely likely" if n == 10 else str(n)))
             for n in range(11)]
    return [
        Question(qid="NPS", text=f"How likely are you to recommend {subject} to a friend or "
                 "colleague?", type=QuestionType.SINGLE, options=scale),
        Question(qid="NPS_WHY", text="What is the main reason for the score you gave?",
                 type=QuestionType.OPEN_TEXT, validation={"probe": True}),
    ]


def _satisfaction(i: Inputs) -> list[Question]:
    subject = i["subject"]
    cols = _scale5("Very dissatisfied", "Very satisfied")
    return [
        Question(qid="SAT_OVERALL", text=f"Overall, how satisfied are you with {subject}?",
                 type=QuestionType.SINGLE, options=cols),
        Question(qid="SAT_ATTR", text=f"And how satisfied are you with {subject} on each of "
                 "the following?", type=QuestionType.GRID_SINGLE,
                 rows=_opts(_lines(i["attributes"])), cols=cols, randomize_options=True),
    ]


def _concept(i: Inputs) -> list[Question]:
    return [
        Question(qid="CONCEPT", text="Please read the following description carefully.\n\n"
                 + i["concept"], type=QuestionType.INFO),
        Question(qid="CON_PI", text="How likely would you be to buy this product if it were "
                 "available where you shop?", type=QuestionType.SINGLE,
                 options=_opts(["Definitely would not buy", "Probably would not buy",
                                "Might or might not buy", "Probably would buy",
                                "Definitely would buy"])),
        Question(qid="CON_UNIQUE", text="How new and different is this product from others "
                 "already available?", type=QuestionType.SINGLE,
                 options=_opts(["Not at all new and different", "Slightly new and different",
                                "Somewhat new and different", "Very new and different",
                                "Extremely new and different"])),
        Question(qid="CON_LIKES", text="What, if anything, do you like about this product?",
                 type=QuestionType.OPEN_TEXT, validation={"probe": True}),
        Question(qid="CON_DISLIKES", text="What, if anything, do you dislike about it?",
                 type=QuestionType.OPEN_TEXT, required=False),
    ]


def _maxdiff(i: Inputs) -> list[Question]:
    items = _lines(i["items"])
    per = 4 if len(items) >= 8 else 3
    screens = math.ceil(3 * len(items) / per)  # each item shown about three times
    return [Question(qid="MAXDIFF", text="Of these, which is the MOST important to you, and "
                     "which is the LEAST important?", type=QuestionType.MAXDIFF,
                     options=_opts(items),
                     validation={"maxdiff": {"screens": screens, "items_per_screen": per}})]


def _pricing(i: Inputs) -> list[Question]:
    p, cur = i["product"], i.get("currency") or ""
    ask = [("VW_TOO_CHEAP", "so cheap that you would doubt its quality"),
           ("VW_CHEAP", "a bargain — a great buy for the money"),
           ("VW_EXPENSIVE", "getting expensive, but you would still consider it"),
           ("VW_TOO_EXPENSIVE", "so expensive that you would not consider buying it")]
    return [Question(qid=qid, text=f"At what price{f' (in {cur})' if cur else ''} would you "
                     f"consider {p} to be {what}?", type=QuestionType.NUMERIC,
                     validation={"min": 0})
            for qid, what in ask]


def _open(i: Inputs) -> list[Question]:
    return [Question(qid="OE", text=i["question"], type=QuestionType.OPEN_TEXT,
                     validation={"probe": True})]


def _demographics(_i: Inputs) -> list[Question]:
    pnts = Option(code=PNTS_CODE, label="Prefer not to say", exclusive=True)
    return [
        Question(qid="D_EMPLOY", text="Which best describes your current employment?",
                 type=QuestionType.SINGLE,
                 options=[*_opts(["Working full-time", "Working part-time", "Self-employed",
                                  "Student", "Not working", "Retired"]), pnts]),
        Question(qid="D_EDU", text="What is the highest level of education you have completed?",
                 type=QuestionType.SINGLE,
                 options=[*_opts(["School", "Diploma or certificate", "Graduate degree",
                                  "Post-graduate degree"]), pnts]),
        Question(qid="D_HH", text="Including yourself, how many people live in your household?",
                 type=QuestionType.NUMERIC, validation={"min": 1, "max": 20}),
    ]


# id: (title, what it is for, inputs, builder)
MODULES: dict[str, tuple[str, str, list[str], Any]] = {
    "consent": ("Consent and notice", "DPDP notice and a consent question; a no ends the "
                "interview.", ["organisation", "purpose", "contact"], _consent),
    "screener": ("Screener", "Age (screens out under the minimum) and gender.",
                 ["min_age"], _screener),
    "hcp_screener": ("Doctor screener", "Specialty, years in practice (2+) and patient load.",
                     ["specialties"], _hcp_screener),
    "awareness": ("Awareness funnel", "Unaided and aided awareness, consideration, purchase, "
                  "preference — asked only of people aware of a brand.",
                  ["category", "brands"], _awareness),
    "nps": ("Net Promoter Score", "0–10 likelihood to recommend, and why (with a follow-up "
            "when the reason is thin).", ["subject"], _nps),
    "satisfaction": ("Satisfaction", "Overall satisfaction and a grid of attributes on a "
                     "balanced 5-point scale.", ["subject", "attributes"], _satisfaction),
    "concept": ("Concept test", "The concept, purchase intent, uniqueness, likes and dislikes.",
                ["concept"], _concept),
    "maxdiff": ("MaxDiff", "Most and least important of each set; the design is built for "
                "you (each item shown about three times).", ["items"], _maxdiff),
    "pricing": ("Van Westendorp pricing", "The four price questions that give an acceptable "
                "range and optimal price.", ["product", "currency"], _pricing),
    "open": ("Open question", "One open question, with a follow-up when the answer is thin.",
             ["question"], _open),
    "demographics": ("Demographics", "Employment, education and household size, each with "
                     "'Prefer not to say'.", [], _demographics),
}

TEMPLATES: dict[str, tuple[str, str, list[str]]] = {
    "brand_tracker": ("Brand tracker", "Who knows, considers, buys and prefers each brand, "
                      "and how satisfied they are — wave after wave.",
                      ["consent", "screener", "awareness", "satisfaction", "nps", "demographics"]),
    "concept_test": ("Concept test", "Reaction to a new idea: appeal, uniqueness, what to fix, "
                     "and the price it can carry.",
                     ["consent", "screener", "concept", "pricing", "demographics"]),
    "usage_attitudes": ("Usage and attitudes", "How a category is bought and used, which brands "
                        "win, and why.", ["consent", "screener", "awareness", "open",
                                          "demographics"]),
    "customer_satisfaction": ("Customer satisfaction", "NPS, satisfaction on what matters, and "
                              "the reasons behind them.",
                              ["consent", "nps", "satisfaction", "open"]),
    "hcp": ("Doctor (HCP) study", "Qualified physicians only; awareness of treatments and what "
            "would change their prescribing.", ["consent", "hcp_screener", "awareness", "open"]),
}


def catalogue() -> dict[str, Any]:
    """Modules and templates with the inputs each asks for, for the builder."""
    def fields(keys: list[str]) -> list[dict[str, str]]:
        return [{"key": k, **FIELDS[k]} for k in keys]

    return {
        "modules": [{"id": m, "title": t, "description": d, "inputs": fields(keys)}
                    for m, (t, d, keys, _f) in MODULES.items()],
        "templates": [{"id": tid, "title": t, "description": d, "modules": mods,
                       "inputs": fields(list(dict.fromkeys(
                           k for m in mods for k in MODULES[m][2])))}
                      for tid, (t, d, mods) in TEMPLATES.items()],
    }


def missing_inputs(module: str, inputs: Inputs) -> list[str]:
    optional = {"min_age", "currency"}
    return [FIELDS[k]["label"] for k in MODULES[module][2]
            if k not in optional and not str(inputs.get(k) or "").strip()]


def _rename(questions: list[Question], taken: set[str]) -> list[Question]:
    """Give each question a free id (`AWR_AIDED_2`), and keep the block's routing on it."""
    mapping: dict[str, str] = {}
    for q in questions:
        new, n = q.qid, 2
        while new in taken or new in mapping.values():
            new, n = f"{q.qid}_{n}", n + 1
        mapping[q.qid] = new

    def fix(e: Expression | None) -> Expression | None:
        if e is None:
            return None
        e.qid = mapping.get(e.qid, e.qid) if e.qid else e.qid
        for c in e.children or []:
            fix(c)
        return e

    for q in questions:
        q.qid = mapping[q.qid]
        q.show_if, q.terminate_if = fix(q.show_if), fix(q.terminate_if)
        for mask in ((q.validation or {}).get("techniques") or {}).get("masks") or []:
            mask["source_qid"] = mapping.get(mask["source_qid"], mask["source_qid"])
    return questions


def build_module(module: str, inputs: Inputs, taken: set[str]) -> list[Question]:
    if module not in MODULES:
        raise ValueError(f"unknown module {module}")
    missing = missing_inputs(module, inputs)
    if missing:
        raise ValueError("needs: " + ", ".join(missing))
    if module == "maxdiff" and not 6 <= len(_lines(inputs["items"])) <= 30:
        raise ValueError("MaxDiff needs between 6 and 30 items")
    return _rename(MODULES[module][3](inputs), taken)


def build_template(template: str, inputs: Inputs, taken: set[str]) -> list[Question]:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template}")
    mods = TEMPLATES[template][2]
    missing = list(dict.fromkeys(m for mod in mods for m in missing_inputs(mod, inputs)))
    if missing:
        raise ValueError("needs: " + ", ".join(missing))
    out: list[Question] = []
    for mod in mods:
        extra = {"health": True} if template == "hcp" else {}
        built = build_module(mod, {**inputs, **extra}, taken | {q.qid for q in out})
        out.extend(built)
    return out
