"""Read the study's instructions the way an analyst does, before the questions.

Every programming questionnaire opens with a brief: who the study is for, how
the sample is split, the quota groups, and scripter notes that change how
later questions behave — "PN: Do not term respondents from Bubble's CRM"
rewrites every screen-out in the survey. An analyst reads that first and keeps
it in mind for every question after. The parsers never did: `study_overview`
lifts numbers out of the brief (N, LOI, quota counts) AFTER every question is
read, and the rules in it reached no question at all.

Understanding prose needs a language model, so this is one — called ONCE per
document, before the question loop. What comes back is held to the document:

  * every claim must quote the instruction line it rests on, and a quote that
    is not in the brief is dropped, not trusted;
  * every question name it cites must exist in the document;
  * nothing here edits a question. A rule the brief states puts a BRIEF_RULE
    flag on each question it governs, so the analyst checks that question —
    the decision stays with a person, and the evidence is the brief's own words.

The result feeds two places: the model calls that read individual questions get
the parts of the understanding that concern that question, and the review page
shows the whole of it first, as "what this survey is about".
"""

from __future__ import annotations

import logging
import re

from src.ai.known_failures import block_for
from src.ai.router import BackendUnavailable, ModelOutputError, run_task
from src.intake.questionnaire_loader import QuestionChunk

logger = logging.getLogger(__name__)

# A brief longer than this is cut, keeping the start — where studies state
# their audience, sample and rules. Real briefs measured 410 to 4,518 chars.
_BRIEF_LIMIT = 12_000
_INDEX_LIMIT = 200
# What one question's prompt receives; the summary plus the rules naming it.
_CONTEXT_LIMIT = 900

_EVIDENCE = {"type": "string", "description": "The instruction line this rests on, copied exactly."}
_QIDS = {"type": "array", "items": {"type": "string"}}

UNDERSTANDING_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "What this survey is about and what it must achieve, 2-4 sentences.",
        },
        "audience": {"type": "string", "description": "Who qualifies to take it."},
        "groups": {
            "type": "array",
            "description": "Sample sources and quota groups the instructions define.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "target": {"type": "string", "description": "Stated n, min/max, or empty."},
                    "measured_by": {**_QIDS, "description": "Question names that identify it."},
                    "evidence": _EVIDENCE,
                },
                "required": ["name", "evidence"],
            },
        },
        "rules": {
            "type": "array",
            "description": "Instructions that change how questions behave or who continues.",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string"},
                    "meaning": {"type": "string", "description": "What it requires, plainly."},
                    "applies_to": {**_QIDS, "description": "Question names it governs."},
                    "evidence": _EVIDENCE,
                },
                "required": ["instruction", "meaning", "evidence"],
            },
        },
        "hidden_variables": {
            "type": "array",
            "description": "Classifications the instructions need that no question asks directly.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "meaning": {"type": "string"},
                    "built_from": _QIDS,
                    "evidence": _EVIDENCE,
                },
                "required": ["name", "meaning", "evidence"],
            },
        },
        "concerns": {
            "type": "array",
            "description": "Contradictions or gaps an analyst would raise with the client.",
            "items": {
                "type": "object",
                "properties": {"issue": {"type": "string"}, "evidence": _EVIDENCE},
                "required": ["issue", "evidence"],
            },
        },
    },
    "required": ["summary"],
}


def brief_text(chunks: list[QuestionChunk]) -> str:
    """The instructions: every stretch of the document that is not a question."""
    parts = [c.chunk_text for c in chunks if c.candidate_qid is None and c.chunk_text.strip()]
    return "\n\n".join(parts)[:_BRIEF_LIMIT]


def _question_index(chunks: list[QuestionChunk]) -> list[str]:
    lines = []
    for c in chunks:
        if c.candidate_qid:
            first = " ".join(c.chunk_text.split())[:110]
            lines.append(f"{c.candidate_qid}: {first}")
    return lines[:_INDEX_LIMIT]


def build_prompt(brief: str, index: list[str]) -> str:
    return (
        "You are a senior market-research analyst opening a programming "
        "questionnaire for the first time. Read the study's instructions below "
        "and write down your understanding of the survey before anyone scripts "
        "it: what it is for, who qualifies, how the sample is split, which "
        "instructions change how questions behave or who continues, which "
        "classifications must be computed, and anything contradictory or missing "
        "that you would raise with the client.\n\n"
        "Rules for your answer:\n"
        "- Every group, rule, hidden variable and concern must quote, in "
        "`evidence`, the instruction line it comes from, copied exactly.\n"
        "- Name questions only from the question list, exactly as written there.\n"
        "- State only what the instructions say or directly imply. Leave a list "
        "empty rather than invent.\n"
        + block_for("understand")
        + "\n"
        f"=== STUDY INSTRUCTIONS ===\n{brief}\n\n"
        "=== QUESTIONS IN THIS DOCUMENT (name: opening words) ===\n" + "\n".join(index)
    )


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-")
    return " ".join(re.sub(r"[^\w'\"<>=%+-]", " ", text).split())


def ground(raw: dict, brief: str, qids: set[str]) -> dict:
    """Keep only what the brief supports; drop the rest and count it."""
    source = _norm(brief)
    known = {q.upper() for q in qids}
    dropped = 0

    def supported(item: dict) -> bool:
        quote = _norm(item.get("evidence", ""))
        return len(quote) >= 6 and quote in source

    def real(names) -> list[str]:
        return [n for n in (names or []) if isinstance(n, str) and n.upper() in known]

    out: dict = {
        "summary": (raw.get("summary") or "").strip(),
        "audience": (raw.get("audience") or "").strip(),
    }
    for key, refs in (("groups", "measured_by"), ("rules", "applies_to"),
                      ("hidden_variables", "built_from"), ("concerns", None)):
        kept = []
        for item in raw.get(key) or []:
            if not isinstance(item, dict) or not supported(item):
                dropped += 1
                continue
            if refs:
                item[refs] = real(item.get(refs))
            kept.append(item)
        out[key] = kept
    out["claims_dropped_unsupported"] = dropped
    return out


def understand_study(chunks: list[QuestionChunk]) -> dict | None:
    """One model reading of the brief, grounded in it. None when there is no
    brief or no model could be reached — extraction carries on without it."""
    brief = brief_text(chunks)
    if len(brief.strip()) < 80:
        return None
    qids = {c.candidate_qid for c in chunks if c.candidate_qid}
    try:
        response = run_task(
            "questionnaire.understand", build_prompt(brief, _question_index(chunks)),
            UNDERSTANDING_SCHEMA,
            # One call per document, writing a long structured answer: give it
            # the room a per-question call does not need.
            timeout=300,
        )
    except (BackendUnavailable, ModelOutputError) as exc:
        logger.warning("study understanding unavailable: %s", exc)
        return {"unavailable": str(exc)[:300]}
    understanding = ground(response.content, brief, qids)
    understanding["model"] = f"{response.backend}/{response.model}"
    return understanding


def context_for(understanding: dict | None, qid: str | None) -> str | None:
    """What the brief says that bears on one question, for its model prompt."""
    if not understanding or not understanding.get("summary"):
        return None
    key = (qid or "").upper()
    lines = [f"About this study: {understanding['summary']}"]
    for rule in understanding.get("rules", []):
        if key and key in {n.upper() for n in rule.get("applies_to", [])}:
            lines.append(f"Instruction for this question: {_rule_text(rule)}")
    for group in understanding.get("groups", []):
        if key and key in {n.upper() for n in group.get("measured_by", [])}:
            lines.append(f"This question identifies the group '{group.get('name', '')}': "
                         f"{group.get('definition', '')}")
    for hidden in understanding.get("hidden_variables", []):
        if key and key in {n.upper() for n in hidden.get("built_from", [])}:
            lines.append(f"Feeds hidden variable {hidden.get('name', '')}: "
                         f"{hidden.get('meaning', '')}")
    return "\n".join(lines)[:_CONTEXT_LIMIT]


def _rule_text(rule: dict) -> str:
    # A model may leave out a field the schema marks required; quote what exists.
    parts = [rule.get("instruction") or rule.get("evidence", ""), rule.get("meaning", "")]
    return " — ".join(p for p in parts if p)


def governing_rules(understanding: dict | None) -> dict[str, list[str]]:
    """Question name -> the brief instructions that govern it."""
    out: dict[str, list[str]] = {}
    for rule in (understanding or {}).get("rules", []):
        for name in rule.get("applies_to", []):
            out.setdefault(name.upper(), []).append(_rule_text(rule))
    return out
