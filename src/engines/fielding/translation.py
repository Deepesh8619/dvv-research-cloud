"""Translations of a questionnaire, through the sheet translators already work in.

A multi-country study is fielded in several languages from one master. The
translator gets a sheet — one row per thing a respondent reads: the question,
each answer, each grid row and column — fills the translation column, and it
comes back in. Checked on the way in: nothing left blank, and every piping or
scripting token (`[pipe: Q1]`, `{Q1}`, `[ASK ALL]`) kept exactly, because a
translator who "translates" a pipe breaks the survey silently.

Stored on the questionnaire (`metadata["translations"][lang]`), so a link
carries the translations it was opened with. A respondent entering with
`?lang=hi` is served the Hindi questionnaire, and `lang` is recorded with
their entry parameters, so the data says which language each answer was in.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from src.core.survey_definition import Question, SurveyDefinition

SHEET_COLUMNS = ("key", "part", "source", "translation", "check")
_TOKEN = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")


def valid_language(code: str) -> bool:
    """A language tag like hi, ta, pt-BR, zh-Hans."""
    return bool(_LANG.match(code or ""))


def _shown(q: Question) -> bool:
    from src.renderers.surveyjs_compiler import _is_respondent_facing

    return not q.qid.upper().startswith("HID_") and _is_respondent_facing(q)


def source_strings(survey: SurveyDefinition) -> list[tuple[str, str, str]]:
    """(key, part, text) for everything a respondent reads, in survey order."""
    out = [("title", "survey title", survey.title or "")]
    for q in survey.questions:
        if not _shown(q):
            continue
        out.append((q.qid, f"{q.qid} question", q.text or ""))
        for kind, part, opts in (("o", "answer", q.options), ("r", "row", q.rows),
                                 ("c", "column", q.cols)):
            for o in opts or []:
                out.append((f"{q.qid}/{kind}/{o.code}", f"{q.qid} {part} {o.code}", o.label))
    return [s for s in out if s[2].strip()]


def sheet(survey: SurveyDefinition, lang: str) -> list[dict[str, str]]:
    """The translator's sheet, with any translation already stored filled in, and
    the model's drafts marked for the translator to check."""
    done = ((survey.metadata or {}).get("translations") or {}).get(lang) or {}
    drafts = set(((survey.metadata or {}).get("translation_drafts") or {}).get(lang) or [])
    return [{"key": k, "part": p, "source": t, "translation": done.get(k, ""),
             "check": "model draft — check" if k in drafts else ""}
            for k, p, t in source_strings(survey)]


def check(survey: SurveyDefinition, texts: dict[str, str]) -> list[dict[str, str]]:
    """What is wrong with a translation: blanks, lost tokens, rows nobody asked for."""
    source = {k: t for k, _p, t in source_strings(survey)}
    problems = []
    for key, text in source.items():
        got = (texts.get(key) or "").strip()
        if not got:
            problems.append({"key": key, "problem": "not translated"})
            continue
        lost = [t for t in _TOKEN.findall(text) if t not in got]
        if lost:
            problems.append({"key": key,
                             "problem": f"keep {', '.join(lost)} exactly as in the source"})
    problems += [{"key": k, "problem": "not in this questionnaire"}
                 for k in texts if k not in source]
    return problems


def store(survey: SurveyDefinition, lang: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the filled rows of a returned sheet; return its problems."""
    texts = {r["key"].strip(): (r.get("translation") or "").strip()
             for r in rows if (r.get("key") or "").strip()}
    problems = check(survey, texts)
    unknown = {p["key"] for p in problems if p["problem"] == "not in this questionnaire"}
    before = (survey.metadata.get("translations") or {}).get(lang) or {}
    survey.metadata.setdefault("translations", {})[lang] = {
        k: v for k, v in texts.items() if v and k not in unknown}
    # A draft stays a draft until a person changes it or clears its check mark.
    still = {r["key"].strip() for r in rows
             if "draft" in (r.get("check") or "") and (r.get("translation") or "").strip()
             == before.get(r["key"].strip())}
    drafts = survey.metadata.setdefault("translation_drafts", {})
    drafts[lang] = sorted(k for k in drafts.get(lang, []) if k in still)
    return problems


def status(survey: SurveyDefinition) -> list[dict[str, Any]]:
    """Per language: how much is translated and how many problems remain."""
    total = len(source_strings(survey))
    out = []
    for lang, texts in sorted(((survey.metadata or {}).get("translations") or {}).items()):
        problems = check(survey, texts)
        blank = sum(p["problem"] == "not translated" for p in problems)
        n_drafts = len(((survey.metadata or {}).get("translation_drafts") or {}).get(lang) or [])
        out.append({"lang": lang, "n_total": total, "n_translated": total - blank,
                    "n_problems": len(problems), "n_drafts": n_drafts})
    return out


def translated(survey: SurveyDefinition, lang: str) -> SurveyDefinition:
    """The questionnaire in `lang`; anything untranslated stays in the source."""
    texts = ((survey.metadata or {}).get("translations") or {}).get(lang)
    if not texts:
        return survey
    out = copy.deepcopy(survey)
    out.title = texts.get("title") or out.title
    for q in out.questions:
        q.text = texts.get(q.qid) or q.text
        for kind, opts in (("o", q.options), ("r", q.rows), ("c", q.cols)):
            for o in opts or []:
                o.label = texts.get(f"{q.qid}/{kind}/{o.code}") or o.label
    out._rebuild_index()
    return out


# --- a model's first draft ---------------------------------------------------

LANGUAGE_NAMES = {
    "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "ur": "Urdu",
    "or": "Odia", "as": "Assamese", "en": "English", "fr": "French", "de": "German",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "pt-BR": "Brazilian Portuguese",
    "nl": "Dutch", "ar": "Arabic", "zh-Hans": "Simplified Chinese", "zh-Hant":
    "Traditional Chinese", "ja": "Japanese", "ko": "Korean", "id": "Indonesian",
    "th": "Thai", "vi": "Vietnamese", "tr": "Turkish", "ru": "Russian", "pl": "Polish",
}
DRAFT_BATCH = 25
_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {
        "type": "object", "properties": {"key": {"type": "string"}, "text": {"type": "string"}},
        "required": ["key", "text"]}}},
    "required": ["translations"],
}
_DRAFT_PROMPT = (
    "Translate these market-research survey texts into {language}. Rules: keep the meaning "
    "and the neutral, plain register of a survey; do not add or drop answer options; copy "
    "every token in square or curly brackets ([pipe: Q1], {{Q2}}, [ASK ALL]) exactly as it is; "
    "keep numbers and brand names. Return JSON with one entry per key.\n\n{items}"
)


def draft(survey: SurveyDefinition, lang: str, run=None) -> dict[str, int]:
    """Fill untranslated strings with a model's draft; never overwrite a person's.

    Each drafted string must keep every token of its source, or it is left blank
    for the translator. Drafted keys are recorded, so the sheet and the panel can
    say which texts still need a person to read them.
    """
    import json

    if run is None:
        from src.ai.router import run_private as run
    language = LANGUAGE_NAMES.get(lang, lang)
    done = survey.metadata.setdefault("translations", {}).setdefault(lang, {})
    drafted = set(survey.metadata.setdefault("translation_drafts", {}).get(lang, []))
    todo = [(k, t) for k, _p, t in source_strings(survey) if not done.get(k)]
    kept = refused = 0
    for i in range(0, len(todo), DRAFT_BATCH):
        batch = dict(todo[i:i + DRAFT_BATCH])
        items = json.dumps([{"key": k, "text": t} for k, t in batch.items()], ensure_ascii=False)
        reply = run(_DRAFT_PROMPT.format(language=language, items=items), _DRAFT_SCHEMA).content
        for item in reply.get("translations") or []:
            key, text = str(item.get("key", "")), str(item.get("text", "")).strip()
            if key not in batch or not text:
                continue
            if any(tok not in text for tok in _TOKEN.findall(batch[key])):
                refused += 1  # a lost pipe breaks the survey; leave it to the translator
                continue
            done[key] = text
            drafted.add(key)
            kept += 1
    survey.metadata["translation_drafts"][lang] = sorted(drafted)
    return {"drafted": kept, "refused": refused, "remaining": len(todo) - kept}
