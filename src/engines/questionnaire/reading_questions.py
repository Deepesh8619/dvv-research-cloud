"""Read the scripter's notes like an analyst: what to build, and what to ask.

A programming document says far more in its notes than in its questions: ~40
variables to create on the Charter tracker alone ("Create dummy variable
SERVICETYPE: INTERNET IF S2=1 …"), groups referred to later by a code they
were never given ("if SERVICETYPE = 3"), lists that live in an Excel file
nobody attached, quality flags that belong in cleaning, a second-language
version of the survey. An analyst reading it stops at each of those. This does
the same, deterministically:

- **variables** — every "create … variable" note, each band compiled to a rule
  (conditions.compile_condition) when its words are exact; otherwise marked
  as needing a person, with the words that did not compile;
- **questions** — what only a person can settle, each with options to click;
- **checks** — quality flags and automatic answers, for cleaning and scripting.

Nothing here changes the questionnaire. Ready variables are offered to create
in one step; questions are asked, never answered on a guess.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import SurveyDefinition
from src.engines.questionnaire.conditions import compile_condition

_NOTE = re.compile(r"^\s*\[(?:PN|PROG(?:RAMMER)? NOTE|SCRIPTER NOTE)\s*:\s*(.*?)\]?\s*$", re.I)
_CREATE = re.compile(
    r"\bcreate\s+(?:a\s+|an\s+)?(?:new\s+)?(?:dummy\s+|hold\s+|hidden\s+|programming\s+)*"
    r"variables?\b\s*(?P<rest>.*)$", re.I)
_NAME_LINE = re.compile(r"^\s*[A-Z][A-Z0-9_]{3,40}\s*$")
_FILE = re.compile(r"\b(?:excel|xlsx?|spreadsheet|csv)\b(?:\s+file)?", re.I)
_BAND = re.compile(r"^\s*(?P<label>[^:=]{1,60}?)\s*(?::|=|\bif\b)\s*(?P<rule>.+)$", re.I)
_CODED_BAND = re.compile(r"^\s*(?P<code>\d{1,3})\s*=\s*(?P<label>.+)$")
_RANGE_BAND = re.compile(r"^\s*(?P<lo>\d{1,3})\s*[-–]\s*(?P<hi>\d{1,3})\s*$"
                         r"|^\s*(?P<plus>\d{1,3})\s*\+\s*$")
_GROUP_REF = re.compile(r"\b([A-Z][A-Z0-9_]{3,40})\s*=\s*(\d{1,2})\b")
_LANGUAGE = re.compile(r"\b(?:remainder|rest) of the survey in (\w+)", re.I)
_QUALITY = re.compile(r"\b(?:quality\s+flag|flag\s+(?:if|any|respondents?))\b", re.I)
_AUTO = re.compile(r"\bauto[- ]?punch", re.I)
_CODE_MISMATCH = re.compile(r"codes as written \((.+?)\) do not match the (\d+) answers")


_NOT_NAMES = {"CALLED", "USING", "BASED", "FILE", "EXCEL", "CAPTURE", "THE", "FOR", "WITH",
              "VARIABLE", "DUMMY", "HOLD", "CREATE", "FROM", "AND", "EACH"}


def _name_in(rest: str) -> str | None:
    """The variable's name: quoted, after "called", or the first ALL-CAPS word
    that is not an instruction word."""
    if m := re.search(r"[“\"'‘]\s*([A-Za-z][A-Za-z0-9_ ]{1,40}?)\s*[”\"'’]", rest):
        return re.sub(r"\W+", "_", m.group(1).strip()).upper()
    if m := re.search(r"\bcalled\s+([A-Za-z][A-Za-z0-9_]{2,40})", rest, re.I):
        return m.group(1).upper()
    caps = [w for w in re.findall(r"\b[A-Z][A-Za-z0-9_]{3,40}\b", rest)
            if w.isupper() or "_" in w or re.search(r"[A-Z].*[A-Z]", w)]
    caps = [w for w in caps if w.upper() not in _NOT_NAMES]
    return caps[0].upper() if caps else None


def _notes(question) -> list[str]:
    return [m.group(1).strip() for line in (question.source_text or "").splitlines()
            if (m := _NOTE.match(line))]


_PUNCH_AT = re.compile(r"(?:selected\s+)?punch(?:es)?\s+(\d[\d\s,]*(?:\s*(?:or|and|-|–)\s*\d+)*)"
                       r"\s+(?:at|in)\s+((?:[A-Za-z]\w*)(?:\s*(?:,|\bor\b)\s*[A-Za-z]\w*(?=\s|,|$))*)",
                       re.I)


def _rule_words(rule: str, host: str | None = None) -> str:
    """The note's wording of a band, rewritten into the condition grammar.

    "Selected punch 1 or 3 at S6a" -> "S6a = 1, 3"; "punch 1 at S5a AND punch 1
    at S7" -> "S5a = 1 and S7 = 1"; "punch 1 or 2 at S5d OR S6b" -> "S5d = 1, 2
    or S6b = 1, 2"; "Did not select punch 1 at S5a OR S6b" -> "S5a != 1 and
    S6b != 1"; "punch 1 or 2" under its own question -> that question.
    """
    text = rule.strip().rstrip(".")
    negated = bool(re.match(r"^\s*did\s+not\s+select", text, re.I))
    text = re.sub(r"^\s*(?:did\s+not\s+)?select(?:ed)?\s+(?=punch)", "", text, flags=re.I)
    if host and re.fullmatch(r"punch(?:es)?\s+[\d\s,orand-]+", text, re.I):
        text += f" at {host}"
    op = "!=" if negated else "="

    def clause(m: re.Match) -> str:
        codes = ", ".join(re.findall(r"\d+", m.group(1)))
        qids = [x for x in re.split(r"\s*(?:,|\bor\b)\s*", m.group(2), flags=re.I) if x]
        return f" {'and' if negated else 'or'} ".join(f"{x.strip()} {op} {codes}" for x in qids)

    rewritten = _PUNCH_AT.sub(clause, text)
    if negated:
        rewritten = re.sub(r"\s+OR\s+", " and ", rewritten)
    return re.sub(r"\s+(AND|OR)\s+", lambda m: f" {m.group(1).lower()} ", rewritten)


def _host_number(question, survey) -> str | None:
    """The numeric question bands like "18-34" refer to: the note's own question."""
    return question.qid if question.type.value == "numeric" else None


def _variables(survey: SurveyDefinition) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for q in survey.questions:
        notes = _notes(q)
        i = 0
        while i < len(notes):
            m = _CREATE.search(notes[i])
            if not m:
                i += 1
                continue
            rest = m.group("rest")
            name = _name_in(rest) or f"{q.qid}_VAR"
            bands, i = [], i + 1
            while i < len(notes) and not _CREATE.search(notes[i]):
                bands.append(notes[i])
                i += 1
            # "SPECTRUMBOTH" / "Spectrum Internet and Mobile = …" / "ATTBOTH" / …:
            # several variables in one note, each a NAME line then its rule.
            heads = [k for k, line in enumerate(bands) if _NAME_LINE.match(line)]
            if len(heads) >= 2:
                for k, start in enumerate(heads):
                    end = heads[k + 1] if k + 1 < len(heads) else len(bands)
                    found.append(_variable(bands[start].strip(), q, rest,
                                           bands[start + 1:end], survey))
                continue
            found.append(_variable(name, q, rest, bands, survey))
    return found


def _variable(name, q, rest, lines, survey) -> dict[str, Any]:
    var: dict[str, Any] = {"name": name, "at": q.qid, "said": rest.strip(), "bands": [],
                           "status": "ready", "why": ""}
    if _FILE.search(rest):
        var.update(status="needs_you", why="built from a file that is not attached")
        return var
    # "based on responses at S2." heads a list; the band lines carry the rules.
    for line in lines:
        band = {"line": line, "label": line, "condition": None}
        if (r := _RANGE_BAND.match(line)) and (host := _host_number(q, survey)):
            lo, hi = (r.group("lo"), r.group("hi")) if r.group("lo") else (r.group("plus"), None)
            words = f"{host} between {lo} and {hi}" if hi else f"{host} >= {lo}"
            band["label"] = line.strip()
        elif c := _CODED_BAND.match(line):
            band.update(code=int(c.group("code")), label=c.group("label").strip())
            words = None
        elif b := _BAND.match(line):
            band["label"] = b.group("label").strip()
            words = _rule_words(b.group("rule"), q.qid)
        else:
            words = None
        if words:
            try:
                band["condition"] = compile_condition(words, survey).to_dict()
            except ValueError as exc:
                band["unread"] = str(exc)
        else:
            band["unread"] = "no rule written for this group"
        var["bands"].append(band)
    labels = [b["label"] for b in var["bands"]]
    if not var["bands"]:
        var.update(status="needs_you", why="no groups are listed under it")
    elif len(set(labels)) < len(labels):
        var.update(status="needs_you",
                   why="the same group is listed twice — this may be two variables in one note")
    elif any(b["condition"] is None for b in var["bands"]):
        unread = [b["label"] for b in var["bands"] if b["condition"] is None]
        var.update(status="needs_you",
                   why=f"no exact rule for {', '.join(unread[:4])}"
                   + ("…" if len(unread) > 4 else ""))
    return var


def _questions(survey, variables) -> tuple[list[dict], list[dict]]:
    asks: list[dict] = []
    checks: list[dict] = []
    defined = {v["name"]: v for v in variables}
    seen_refs: set[str] = set()
    for q in survey.questions:
        for note in _notes(q):
            if m := _CODE_MISMATCH.search(note):
                asks.append({"qid": q.qid, "kind": "codes",
                             "ask": f"{q.qid}'s code box reads \"{m.group(1)}\" but it has "
                                    f"{m.group(2)} answers. Which codes go with which answers?",
                             "options": ["Number the answers in order", "I will set the codes"]})
            other = [x for x in _LANGUAGE.findall(note) if x.lower() != "english"]
            if other:
                lang = other[0].capitalize()
                asks.append({"qid": q.qid, "kind": "language",
                             "ask": f"{q.qid} switches the rest of the survey to {lang}. "
                                    f"Do you have the {lang} wording?",
                             "options": [f"I will add the {lang} translation",
                                         "Field in English only"]})
            for ref, code in _GROUP_REF.findall(note):
                var = defined.get(ref)
                if var and not any("code" in b for b in var["bands"]) and ref not in seen_refs:
                    seen_refs.add(ref)
                    n = int(code)
                    guess = var["bands"][n - 1]["label"] if 0 < n <= len(var["bands"]) else None
                    asks.append({"qid": q.qid, "kind": "group_code",
                                 "ask": f"Notes test {ref} = {code}, but {ref}'s groups are listed "
                                        "without codes. Are they numbered in the order listed?",
                                 "options": ([f"Yes — {code} is {guess}"] if guess else [])
                                            + ["No, I will say which"]})
            if _QUALITY.search(note):
                checks.append({"qid": q.qid, "kind": "quality", "said": note})
            if _AUTO.search(note):
                checks.append({"qid": q.qid, "kind": "auto_answer", "said": note})
    # One question per KIND of gap: ten "NUMBEROF…SERVICES" counting rules are
    # one decision, not ten.
    groups: dict[str, list[dict]] = {}
    for var in variables:
        if var["status"] == "needs_you":
            key = re.sub(r"\b[A-Z]{2,}(?=SERVICES\b)", "", var["why"])
            groups.setdefault(f"{var['at']}|{key}", []).append(var)
    for grouped in groups.values():
        first, names = grouped[0], [v["name"] for v in grouped]
        listed = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        asks.append({"qid": first["at"], "kind": "variable", "variables": names,
                     "ask": f"{listed} ({first['at']}): {first['why']}. How should "
                            f"{'they' if len(names) > 1 else 'it'} be built?",
                     "options": ["Leave for the scripter", "I will write the rule"]
                     + (["I will upload the file"] if "file" in first["why"] else [])})
    return asks, checks


def analyst_reading(survey: SurveyDefinition) -> dict[str, Any]:
    """Variables to build, questions to ask, checks to add — from the notes."""
    variables = _variables(survey)
    asks, checks = _questions(survey, variables)
    return {
        "variables": variables,
        "ready": sum(1 for v in variables if v["status"] == "ready"),
        "questions": asks,
        "checks": checks,
    }
