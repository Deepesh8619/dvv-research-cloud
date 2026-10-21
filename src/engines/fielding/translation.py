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

SHEET_COLUMNS = ("key", "part", "source", "translation")
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
    """The translator's sheet, with any translation already stored filled in."""
    done = ((survey.metadata or {}).get("translations") or {}).get(lang) or {}
    return [{"key": k, "part": p, "source": t, "translation": done.get(k, "")}
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
    survey.metadata.setdefault("translations", {})[lang] = {
        k: v for k, v in texts.items() if v and k not in unknown}
    return problems


def status(survey: SurveyDefinition) -> list[dict[str, Any]]:
    """Per language: how much is translated and how many problems remain."""
    total = len(source_strings(survey))
    out = []
    for lang, texts in sorted(((survey.metadata or {}).get("translations") or {}).items()):
        problems = check(survey, texts)
        blank = sum(p["problem"] == "not translated" for p in problems)
        out.append({"lang": lang, "n_total": total, "n_translated": total - blank,
                    "n_problems": len(problems)})
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
