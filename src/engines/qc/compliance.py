"""Pharma compliance: adverse events in open ends, and the notice a respondent agrees to.

**Adverse events.** In health research, a side effect or a medicine failing for
a person a respondent describes must reach the drug's maker within a working
day (BHBIA / EphMRA guidance). Agencies find them by reading every verbatim.
A word list alone cries wolf: on two physician studies every hit for "side
effect" or "safety" was an opinion ("efficacy and safety", "expand on
hallucinations"), none an event. So an answer is a *possible adverse event*
only when someone experienced it ("my patient developed a rash", "it did not
work for him"); an event word with nobody experiencing it, or denied ("no side
effects"), is listed as a *mention*. Both lists go to a person; nothing is
reported or dismissed automatically. All interviews are read, screen-outs and
partials included: an event told in a terminated interview still counts.

**The notice.** India's DPDP Act (s.5) and Rules (r.3) require a notice before
consent: the personal data and its purpose, how to withdraw consent, how to
exercise rights and raise a grievance, and that a complaint can go to the Data
Protection Board. Neither physician questionnaire carried a consent question,
a notice or an adverse-event statement; `notice_questions` writes them.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from src.core.models import DataMap, VarType
from src.core.survey_definition import Expression, Option, Question, QuestionType

# An event, or a medicine not doing its job (lack of efficacy, overdose,
# pregnancy exposure and misuse are reportable "special situations").
_EVENT = re.compile(
    r"\b(side[- ]?effects?|adverse (?:events?|reactions?|effects?)|reactions?|rash\w*|"
    r"nause\w*|vomit\w*|dizz\w*|headaches?|hallucinat\w*|hospitali[sz]\w*|"
    r"died|death|passed away|suicid\w*|allerg\w*|seizures?|overdos\w*|pregnan\w*|"
    r"swelling|bleeding|palpitations?|did ?n[o']?t work|stopped working|"
    r"not effective|ineffective|no effect|took too much|wrong dose|misuse\w*)\b",
    re.I,
)
# Someone had it: a person named, or a verb of experiencing.
_EXPERIENCED = re.compile(
    r"\b(?:my|a|one|the|this|that|two|three|several|some) (?:patients?|clients?|son|daughter|"
    r"wife|husband|mother|father|child|friend)\b|"
    r"\b(?:i|he|she|they|we|patient|patients) (?:had|have|has|got|felt|experienced|developed|"
    r"suffered|ended up|was|were)\b|"
    r"\b(?:experienced|developed|suffered|complained of|after (?:taking|starting|using|"
    r"the (?:first |second )?dose))\b|"
    r"\bwork(?:ed)? for (?:me|him|her|them|my)\b",
    re.I,
)
_DENIED = re.compile(r"\b(?:no|without|never|not any|zero|free of)\b(?:\W+\w+){0,2}\W*$", re.I)
# Script, hidden and design columns are never a respondent's words.
_NOT_ANSWERS = ("system", "hidden", "maxdiff")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


def classify(answer: str) -> tuple[str, list[str], str] | None:
    """("possible" | "mention", event words, the sentence) or None."""
    best = None
    for sentence in _sentences(answer):
        hits = list(_EVENT.finditer(sentence))
        if not hits:
            continue
        terms = sorted({m.group(0).lower() for m in hits})
        denied = all(_DENIED.search(sentence[: m.start()]) for m in hits)
        if _EXPERIENCED.search(sentence) and not denied:
            return "possible", terms, sentence
        best = best or ("mention", terms, sentence)
    return best


def scan_adverse_events(df: pl.DataFrame, datamap: DataMap, keys: list[str]) -> dict[str, Any]:
    """Every open answer naming an event, split into possible events and mentions."""
    from src.engines.qc.variable_roles import classify_variable

    columns = [v for v in datamap.variables
               if v.type == VarType.TEXT and v.name in df.columns
               and v.provenance != "fielding_paradata"
               and classify_variable(v.name, v.label or "", v.question_ref) not in _NOT_ANSWERS]
    found: dict[str, list[dict]] = {"possible": [], "mention": []}
    for v in columns:
        for i, value in enumerate(df[v.name].to_list()):
            if not isinstance(value, str) or not (hit := classify(value)):
                continue
            tier, terms, sentence = hit
            found[tier].append({
                "respondent": keys[i], "column": v.name,
                "question": (v.question_text or v.label or "")[:200],
                "terms": terms, "sentence": sentence[:400], "answer": value[:2000],
            })
    return {"n_respondents": df.height, "n_open_ends": len(columns),
            "possible": found["possible"], "mentions": found["mention"]}


# --- the notice ---------------------------------------------------------------

_HEALTH = re.compile(
    r"\b(patients?|physicians?|doctors?|hcps?|prescri\w*|medications?|medicines?|drugs?|"
    r"therap\w*|pharmac\w*|clinic\w*|dose|dosing)\b", re.I)
_AE_STATEMENT = re.compile(
    r"pharmacovigilance|adverse events? report|report(?:ing)? (?:of |any )?adverse events?|"
    r"(?:obliged|required|obligated) to (?:report|pass on|forward)", re.I)
_CONSENT = re.compile(r"\bconsent\b|agree to (?:take part|participate)", re.I)
NOTICE_ITEMS = (
    ("purpose", "what the data is for", re.compile(r"\bpurpose\b|used (?:only )?(?:for|to)", re.I)),
    ("data", "what personal data is collected",
     re.compile(r"personal (?:data|information)|we (?:will )?collect", re.I)),
    ("withdraw", "how to withdraw consent", re.compile(r"\bwithdraw", re.I)),
    ("grievance", "who to contact for rights and grievances",
     re.compile(r"grievance|data protection officer|contact (?:us|the)|\S+@\S+\.\w", re.I)),
    ("board", "that a complaint can go to the Data Protection Board",
     re.compile(r"data protection board", re.I)),
)


def is_consent(q: Question) -> bool:
    """A question respondents agree to take part on."""
    if (q.validation or {}).get("consent") is True:
        return True
    return q.type is QuestionType.SINGLE and bool(_CONSENT.search(q.text or ""))


def check_notice(survey) -> dict[str, Any]:
    """What a respondent is told before agreeing, against what the law asks."""
    text = "\n".join(q.text or "" for q in survey.questions)
    health = len(_HEALTH.findall(text)) >= 3
    items = [{"item": "consent", "label": "a question respondents agree on",
              "present": any(is_consent(q) for q in survey.questions)}]
    items += [{"item": key, "label": label, "present": bool(rx.search(text))}
              for key, label, rx in NOTICE_ITEMS]
    if health:
        items.append({"item": "adverse_events",
                      "label": "that side effects they describe will be reported",
                      "present": bool(_AE_STATEMENT.search(text))})
    return {"health_study": health, "items": items,
            "complete": all(i["present"] for i in items),
            "consent_questions": [q.qid for q in survey.questions if is_consent(q)]}


NOTICE_QID, CONSENT_QID = "DPDP_NOTICE", "CONSENT"


def notice_questions(organisation: str, purpose: str, contact: str,
                     health: bool) -> list[Question]:
    """The notice screen and the consent question that ends the interview on a no."""
    text = (
        f"{organisation} is carrying out this research for the purpose of {purpose}. "
        "If you take part, we will collect your answers to this survey and the technical "
        "details of your device needed to run it. This personal data is used only for this "
        "research, reported in total, and never sold. You may withdraw your consent at any "
        f"time, as easily as you give it, by contacting {contact}; your answers will then be "
        f"erased. You can ask us to see, correct or erase your data, or raise a grievance, at "
        f"{contact}. If you are not satisfied with our reply, you may complain to the Data "
        "Protection Board of India."
    )
    if health:
        text += (
            " This survey is about health and medicines. If you tell us about a side effect "
            "or other problem a specific patient (or you) had while taking a medicine, we are "
            "required to report it to the company that makes the medicine, for safety "
            "monitoring (pharmacovigilance). Your answers otherwise stay confidential."
        )
    return [
        Question(qid=NOTICE_QID, text=text, type=QuestionType.INFO, options=[]),
        Question(
            qid=CONSENT_QID,
            text="Do you agree to take part, and to the use of your personal data "
                 "as described above?",
            type=QuestionType.SINGLE,
            options=[Option(code=1, label="Yes, I agree"),
                     Option(code=2, label="No, I do not agree")],
            required=True,
            terminate_if=Expression(op="eq", qid=CONSENT_QID, value=2,
                                    verbatim_text="TERMINATE if does not agree"),
            validation={"consent": True},
        ),
    ]
