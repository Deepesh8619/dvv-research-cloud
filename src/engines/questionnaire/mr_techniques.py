"""Market-research techniques as written in real scripter directives.

These are the conventions a questionnaire uses to say "this question's option
list comes from another question", "put an earlier answer into this wording",
or "balance respondents across design versions". They are not decoration: a
masked question that shows the wrong options collects unusable data, and it is
invisible in the output unless something checks it.

Every pattern below is taken verbatim from three real client questionnaires:

    masking     [PN: ONLY DISPLAY OPTIONS SELECTED AT S6]
                [PN: SHOW ONLY ITEMS SELECTED AT Q18 IN ROWS]
                [SHOW Q26 LIST]
                [SHOW Q26 LIST EXCEPT FOR Q27 SELECTION]
                Unaware (Not selected at Q2a)
    piping      [INSERT RESPONSE FROM Q8]      [INSERT Q7 #1 RANK]
                [INSERT PHARMACY]              [INSERT RATING]
    least fill  ASSIGN BASED ON LEAST FILL
                ON A ROTATED AND LEAST FILLED BASIS

Masking is the one worth modelling structurally rather than recording as
prose, because it can be CHECKED against collected data: a respondent's answer
to a masked question may only contain codes they chose at the source question.
Nothing else in the pipeline can catch a mis-scripted mask.

Deterministic and dependency-free; no model is called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A question label as these documents write it: Q18, S6, Q2a, Q13a, P1.
_QID = r"[A-Za-z]{0,3}\d+[a-z]?"

# --- Masking ---------------------------------------------------------------
# Ordered: the most specific wording must win. "SHOW Q26 LIST EXCEPT FOR Q27
# SELECTION" also matches the plain "SHOW Q26 LIST" pattern.
_MASK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            rf"SHOW\s+(?P<source>{_QID})\s+LIST\s+EXCEPT\s+FOR\s+(?P<exclude>{_QID})",
            re.I,
        ),
        "list_except",
    ),
    (
        re.compile(
            rf"(?:ONLY\s+)?(?:DISPLAY|SHOW)\s+ONLY\s+(?:OPTIONS|ITEMS|CODES)?\s*"
            rf"SELECTED\s+(?:AT|IN)\s+(?P<source>{_QID})",
            re.I,
        ),
        "selected",
    ),
    (
        re.compile(
            rf"ONLY\s+(?:DISPLAY|SHOW)\s+(?:OPTIONS|ITEMS|CODES)?\s*"
            rf"SELECTED\s+(?:AT|IN)\s+(?P<source>{_QID})",
            re.I,
        ),
        "selected",
    ),
    (
        re.compile(rf"NOT\s+SELECTED\s+(?:AT|IN)\s+(?P<source>{_QID})", re.I),
        "not_selected",
    ),
    (
        re.compile(rf"SHOW\s+(?P<source>{_QID})\s+LIST", re.I),
        "full_list",
    ),
    (
        # "INSERT BRANDS USED WITHIN PAST 12 MONTHS FROM Q13" builds an option
        # LIST. "INSERT RESPONSE FROM Q8" puts ONE answer into the wording —
        # that is a pipe, not a mask, and treating it as a mask would claim the
        # question's options come from Q8 when they do not. The plural/list
        # noun is what separates them.
        re.compile(
            rf"INSERT\s+(?!RESPONSE\b|RATING\b|SELECTION\b)"
            rf"[A-Za-z0-9 ,'’/-]{{0,60}}?"
            rf"\b(?:BRANDS|ITEMS|OPTIONS|CODES|STATEMENTS|ANSWERS|LIST)\b"
            rf"[A-Za-z0-9 ,'’/-]{{0,40}}?FROM\s+(?P<source>{_QID})",
            re.I,
        ),
        "selected",
    ),
]

# --- Piping ----------------------------------------------------------------
# A qid-bearing pipe can be resolved; a named one ("[INSERT PHARMACY]") refers
# to a scripter-side variable and is recorded, never guessed at.
_PIPE_QID_RE = re.compile(rf"INSERT\s+(?:RESPONSE\s+(?:FROM|FOR)\s+)?(?P<source>{_QID})\b", re.I)
_PIPE_NAMED_RE = re.compile(r"INSERT\s+(?P<name>[A-Z][A-Z ]{2,30}?)\s*\]", re.I)
# "[#1 RANK FROM Q8]" — a pipe written without the word INSERT. Both other
# forms require it, so this one was not read as a pipe at all: the compiler
# then stripped it as an unknown scripter tag and the respondent was asked
# "Why do you rank as your most preferred pharmacy?" — a sentence with the
# subject deleted, answered as an open end.
_PIPE_RANK_RE = re.compile(
    rf"#?\s*(?P<rank>\d+)\s*(?:ST|ND|RD|TH)?\s+RANK(?:ED|ING)?\s+(?:FROM|AT|IN)\s+"
    rf"(?P<source>{_QID})\b",
    re.I,
)

# --- Least fill ------------------------------------------------------------
_LEAST_FILL_RE = re.compile(r"least[\s-]?fill(?:ed)?", re.I)


@dataclass(frozen=True)
class Mask:
    """This question's options come from another question's answers."""

    source_qid: str
    #: selected | not_selected | full_list | list_except
    mode: str
    exclude_qid: str | None = None
    verbatim: str = ""

    def to_dict(self) -> dict:
        return {
            "source_qid": self.source_qid,
            "mode": self.mode,
            "exclude_qid": self.exclude_qid,
            "verbatim": self.verbatim,
        }


@dataclass(frozen=True)
class Pipe:
    """An earlier answer substituted into this question's wording."""

    #: The question piped from, when the directive names one.
    source_qid: str | None
    #: The scripter-side name, when it does not ("PHARMACY", "RATING").
    name: str | None
    verbatim: str = ""

    def to_dict(self) -> dict:
        return {"source_qid": self.source_qid, "name": self.name, "verbatim": self.verbatim}


@dataclass
class Techniques:
    """MR techniques declared by one question's text and directives."""

    masks: list[Mask]
    pipes: list[Pipe]
    least_fill: bool

    def to_dict(self) -> dict:
        return {
            "masks": [m.to_dict() for m in self.masks],
            "pipes": [p.to_dict() for p in self.pipes],
            "least_fill": self.least_fill,
        }

    def __bool__(self) -> bool:
        return bool(self.masks or self.pipes or self.least_fill)


def detect_techniques(*fragments: str) -> Techniques:
    """Find masking, piping and least-fill declarations in a question's text.

    Pass the wording and every bracketed directive. Anything recognised is
    returned structurally; anything not recognised is simply absent, never
    guessed at — a wrong mask is worse than a missing one, because it looks
    like the question was understood.
    """
    masks: list[Mask] = []
    pipes: list[Pipe] = []
    least_fill = False
    seen_masks: set[tuple[str, str]] = set()
    seen_pipes: set[tuple[str | None, str | None]] = set()

    for fragment in fragments:
        if not fragment:
            continue
        if _LEAST_FILL_RE.search(fragment):
            least_fill = True

        for pattern, mode in _MASK_PATTERNS:
            match = pattern.search(fragment)
            if not match:
                continue
            source = match.group("source").upper()
            key = (source, mode)
            if key in seen_masks:
                break
            seen_masks.add(key)
            groups = match.groupdict()
            masks.append(
                Mask(
                    source_qid=source,
                    mode=mode,
                    exclude_qid=(groups.get("exclude") or "").upper() or None,
                    verbatim=match.group(0).strip(),
                )
            )
            break  # most specific pattern wins; do not double-count

        for match in _PIPE_QID_RE.finditer(fragment):
            source = match.group("source").upper()
            if (source, None) not in seen_pipes:
                seen_pipes.add((source, None))
                pipes.append(Pipe(source_qid=source, name=None, verbatim=match.group(0).strip()))
        for match in _PIPE_RANK_RE.finditer(fragment):
            source = match.group("source").upper()
            if (source, None) not in seen_pipes:
                seen_pipes.add((source, None))
                pipes.append(
                    Pipe(source_qid=source, name=None, verbatim=match.group(0).strip())
                )
        for match in _PIPE_NAMED_RE.finditer(fragment):
            name = match.group("name").strip().upper()
            # "INSERT Q7 #1 RANK" already matched the qid form.
            if re.match(rf"^{_QID}$", name, re.I):
                continue
            if (None, name) not in seen_pipes:
                seen_pipes.add((None, name))
                pipes.append(Pipe(source_qid=None, name=name, verbatim=match.group(0).strip()))

    return Techniques(masks=masks, pipes=pipes, least_fill=least_fill)
