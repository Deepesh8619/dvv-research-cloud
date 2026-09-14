"""Pure question-lookup helpers backing the Steward specialist's questionnaire tools.

These functions operate on an already-loaded ``SurveyDefinition`` (and, where
useful, a ``DataMap``) and return plain JSON-serialisable dicts. They contain no
I/O, no DB access, and no LLM calls — the disk/DB lookup that finds a study's
approved questionnaire lives in ``api/routes/chat.py`` (which reuses the same
file-path convention as the questionnaire route). Keeping the logic pure here
makes it directly unit-testable without a database or files on disk.

Design note: each helper takes ``survey: SurveyDefinition | None``. ``None`` means
"this study has no approved questionnaire" — the common case, since most studies
in this system predate the questionnaire feature. Every helper degrades to a
clear ``{"success": False, "available": False, ...}`` result in that case rather
than raising, so the navigator's tool-calling loop gets a usable string back
instead of a stack trace surfacing to the analyst.
"""

from __future__ import annotations

from src.core.survey_definition import (
    Expression,
    Question,
    QuestionVariableLink,
    SurveyDefinition,
)

# Human-readable phrasing for the leaf comparison operators, used when we have to
# fall back to describing a structured show_if because no verbatim routing prose
# was captured during extraction.
_OP_PHRASING: dict[str, str] = {
    "eq": "answered {value} to {qid}",
    "ne": "did NOT answer {value} to {qid}",
    "gt": "answered more than {value} to {qid}",
    "lt": "answered less than {value} to {qid}",
    "in": "answered any of {values} to {qid}",
    "selected": "selected option {value} in {qid}",
}


def _no_questionnaire_result(tool: str, study_id: str) -> dict:
    """Uniform 'no approved questionnaire for this study' result."""
    return {
        "success": False,
        "available": False,
        "tool": tool,
        "study_id": study_id,
        "message": (
            "No approved questionnaire is available for this study. Most studies "
            "predate the questionnaire feature; upload and approve one to enable "
            "question-level lookups."
        ),
    }


def _question_not_found_result(
    tool: str, study_id: str, qid: str, survey: SurveyDefinition
) -> dict:
    """Uniform 'qid not in the approved questionnaire' result, listing valid qids."""
    known = [q.qid for q in survey.questions]
    return {
        "success": False,
        "available": True,
        "tool": tool,
        "study_id": study_id,
        "qid": qid,
        "message": f"Question '{qid}' is not in this study's approved questionnaire.",
        "known_qids": known,
    }


def get_question_details(
    survey: SurveyDefinition | None,
    study_id: str,
    qid: str,
    links: list[QuestionVariableLink] | None = None,
) -> dict:
    """Full extracted Question for one qid: text, type, options, routing, flags.

    Returns the question's own ``to_dict()`` (which already includes options with
    codes/labels, ``show_if``/``terminate_if`` with their verbatim routing text,
    per-field extraction confidence, and any flags) so the navigator can explain
    the question exactly as extracted. When precomputed
    ``link_questions_to_datamap`` results are supplied, also reports which DataMap
    variable(s) back this question. Degrades gracefully when the study has no
    questionnaire or the qid is unknown.
    """
    if survey is None:
        return _no_questionnaire_result("get_question_details", study_id)
    if qid not in survey:
        return _question_not_found_result("get_question_details", study_id, qid, survey)

    question = survey[qid]
    result = {
        "success": True,
        "available": True,
        "tool": "get_question_details",
        "study_id": study_id,
        "qid": qid,
        "question": question.to_dict(),
    }
    # Surface the T16 extraction/verification failure diagnostic at the top level
    # so an analyst asking the navigator "why did this question fail?" gets the
    # real root cause (e.g. "GROQ_API_KEY not set"), not just a flag. Each is
    # {"error_type", "error_detail"} stashed on Question.validation, or None.
    validation = question.validation or {}
    extraction_error = validation.get("extraction_error")
    verification_error = validation.get("verification_error")
    if extraction_error is not None:
        result["extraction_error"] = extraction_error
    if verification_error is not None:
        result["verification_error"] = verification_error
    linked = question_variable_links(qid, links)
    if linked is not None:
        result["datamap_variables"] = linked
    return result


def _describe_expression(expr: Expression) -> str:
    """Plain-English description of a structured routing Expression.

    Used only as a fallback/cross-check when no ``verbatim_text`` was captured —
    the verbatim routing prose is always preferred because it is already the
    human wording the survey author wrote.
    """
    op = expr.op
    if op in ("and", "or"):
        joiner = " AND " if op == "and" else " OR "
        parts = [_describe_expression(c) for c in expr.children or []]
        return "(" + joiner.join(parts) + ")" if parts else "(no condition)"
    if op == "not":
        parts = [_describe_expression(c) for c in expr.children or []]
        inner = parts[0] if parts else "the condition"
        return f"NOT ({inner})"

    template = _OP_PHRASING.get(op)
    if template is None:
        return f"a {op} condition on {expr.qid or 'a prior question'}"
    return "the respondent " + template.format(
        value=expr.value, values=expr.values, qid=expr.qid or "a prior question"
    )


def _verbatim_from_expression(expr: Expression) -> str | None:
    """First verbatim_text found anywhere in the expression tree, or None.

    Extraction attaches the routing prose to the outermost expression, but a
    compound show_if may carry it on a child instead, so we search the whole
    tree and return the first non-empty one.
    """
    if expr.verbatim_text and expr.verbatim_text.strip():
        return expr.verbatim_text.strip()
    for child in expr.children or []:
        found = _verbatim_from_expression(child)
        if found:
            return found
    return None


def explain_question_base(
    survey: SurveyDefinition | None, study_id: str, qid: str
) -> dict:
    """Explain WHO was asked a question, in stakeholder-friendly plain English.

    Translates the question's ``show_if`` into prose, preferring the verbatim
    routing text captured at extraction (already human-readable) and falling back
    to a description synthesised from the structured expression when no verbatim
    was captured. A question with no ``show_if`` is reported as asked of all
    respondents who reached it. Degrades gracefully when the study has no
    questionnaire or the qid is unknown.
    """
    if survey is None:
        return _no_questionnaire_result("explain_question_base", study_id)
    if qid not in survey:
        return _question_not_found_result("explain_question_base", study_id, qid, survey)

    question = survey[qid]
    show_if = question.show_if

    if show_if is None:
        explanation = (
            "Asked of all respondents who reached this point in the survey "
            "(the question has no routing condition of its own)."
        )
        return {
            "success": True,
            "available": True,
            "tool": "explain_question_base",
            "study_id": study_id,
            "qid": qid,
            "conditional": False,
            "explanation": explanation,
            "verbatim_routing": None,
            "structured_show_if": None,
        }

    verbatim = _verbatim_from_expression(show_if)
    structured = _describe_expression(show_if)
    if verbatim:
        explanation = f"Asked only of respondents where: {verbatim}"
    else:
        explanation = f"Asked only of respondents where {structured}."

    return {
        "success": True,
        "available": True,
        "tool": "explain_question_base",
        "study_id": study_id,
        "qid": qid,
        "conditional": True,
        "explanation": explanation,
        # Both surfaced so the navigator (or a reviewer) can cross-check the
        # plain-English base against the structured condition.
        "verbatim_routing": verbatim,
        "structured_show_if": structured,
    }


def _routing_verbatim_texts(question: Question) -> list[str]:
    """All verbatim routing snippets on a question (show_if + terminate_if)."""
    texts: list[str] = []
    for expr in (question.show_if, question.terminate_if):
        if expr is not None:
            found = _verbatim_from_expression(expr)
            if found:
                texts.append(found)
    return texts


def _snippet(haystack: str, needle: str, radius: int = 40) -> str:
    """A short context window around the first match of `needle` in `haystack`."""
    low = haystack.lower()
    pos = low.find(needle.lower())
    if pos == -1:
        return haystack[: radius * 2].strip()
    start = max(0, pos - radius)
    end = min(len(haystack), pos + len(needle) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(haystack) else ""
    return f"{prefix}{haystack[start:end].strip()}{suffix}"


def search_questionnaire(
    survey: SurveyDefinition | None, study_id: str, query: str
) -> dict:
    """Free-text search over question text (and verbatim routing text).

    Lets the navigator find "the question about X" without the analyst knowing the
    qid up front. Case-insensitive substring match against each question's ``text``
    and any verbatim routing prose; returns matching qids with a short snippet
    showing where the query hit. Degrades gracefully when the study has no
    questionnaire; an empty/whitespace query returns no matches rather than
    everything.
    """
    if survey is None:
        return _no_questionnaire_result("search_questionnaire", study_id)

    needle = (query or "").strip()
    if not needle:
        return {
            "success": True,
            "available": True,
            "tool": "search_questionnaire",
            "study_id": study_id,
            "query": query,
            "matches": [],
            "message": "Empty search query; provide text to search for.",
        }

    low_needle = needle.lower()
    matches: list[dict] = []
    for question in survey.questions:
        text = question.text or ""
        routing_texts = _routing_verbatim_texts(question)
        where = None
        snippet = None
        if low_needle in text.lower():
            where = "question_text"
            snippet = _snippet(text, needle)
        else:
            for rt in routing_texts:
                if low_needle in rt.lower():
                    where = "routing_text"
                    snippet = _snippet(rt, needle)
                    break
        if where is not None:
            matches.append(
                {
                    "qid": question.qid,
                    "text": text,
                    "matched_in": where,
                    "snippet": snippet,
                }
            )

    return {
        "success": True,
        "available": True,
        "tool": "search_questionnaire",
        "study_id": study_id,
        "query": query,
        "matches": matches,
        "n_matches": len(matches),
    }


def _qid_for_variable(
    stub: str, links: list[QuestionVariableLink] | None
) -> str | None:
    """Reverse lookup: which question produced a given DataMap variable stub.

    Scans precomputed ``link_questions_to_datamap`` results for the link whose
    ``variable_names`` includes ``stub`` and returns its qid. Returns None when no
    link claims the variable — the common case for system/paradata columns that
    no questionnaire question produced. Match is case-insensitive to tolerate the
    normalization the linker applies when matching names.
    """
    target = (stub or "").strip().upper()
    if not target:
        return None
    for link in links or []:
        if any((name or "").strip().upper() == target for name in link.variable_names):
            return link.qid
    return None


def get_question_context(
    survey: SurveyDefinition | None,
    study_id: str,
    stub: str,
    links: list[QuestionVariableLink] | None = None,
) -> dict:
    """Resolve a DataMap variable back to the questionnaire question it came from.

    The Analyst sees only collected data variable stubs/labels; this bridges back
    to the instrument so a crosstab on ``Q5r1`` can be described as "Q5, 'Which
    brands are you aware of', asked only of respondents who passed the awareness
    screener" — explaining, for instance, why a base size is small (routed/skipped,
    not merely low response).

    Uses the precomputed link data (reverse lookup — which question's
    ``variable_names`` include this stub) to find the source qid, then reuses
    ``explain_question_base``'s routing/base prose rather than duplicating it.
    Degrades gracefully to ``available: False`` / ``linked: False`` when the study
    has no questionnaire, or the variable isn't linked to any question (paradata,
    system fields, or an unmatched column).
    """
    if survey is None:
        return _no_questionnaire_result("get_question_context", study_id)

    qid = _qid_for_variable(stub, links)
    if qid is None or qid not in survey:
        return {
            "success": True,
            "available": True,
            "linked": False,
            "tool": "get_question_context",
            "study_id": study_id,
            "stub": stub,
            "message": (
                f"Variable '{stub}' is not linked to any questionnaire question "
                "(it may be a system/paradata field, or the study's questionnaire "
                "doesn't cover it)."
            ),
        }

    question = survey[qid]
    # Reuse explain_question_base for the plain-English routing/base description
    # instead of re-deriving it — same wording the Steward's base tool produces.
    base = explain_question_base(survey, study_id, qid)
    return {
        "success": True,
        "available": True,
        "linked": True,
        "tool": "get_question_context",
        "study_id": study_id,
        "stub": stub,
        "qid": qid,
        "text": question.text,
        "type": question.type.value,
        "conditional": base.get("conditional"),
        "base_explanation": base.get("explanation"),
        "verbatim_routing": base.get("verbatim_routing"),
    }


def question_variable_links(
    qid: str, links: list[QuestionVariableLink] | None
) -> dict | None:
    """The DataMap variable(s) backing a question, from precomputed link data.

    Optional enrichment for ``get_question_details``: when ``link_questions_to_datamap``
    (src/engines/questionnaire/linker.py) has been run for this study, surface which
    collected variables a question produced. Returns None when there is no link for
    the qid (or no links at all), so the caller can simply omit the field.
    """
    for link in links or []:
        if link.qid == qid:
            return {
                "variable_names": list(link.variable_names),
                "match_method": link.match_method,
                "confidence": link.confidence,
            }
    return None
