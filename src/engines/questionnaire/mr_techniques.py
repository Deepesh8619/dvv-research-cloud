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
        re.compile(rf"NOT\s+(?:SELECTED|CHOSEN|PICKED|USED|MENTIONED)\s+(?:AT|IN)\s+(?P<source>{_QID})", re.I),  # noqa: E501
        "not_selected",
    ),
    (
        # "Show all response options EXCEPT for option selected at S6".
        re.compile(rf"\bEXCEPT\s+(?:FOR\s+)?(?:THE\s+)?(?:OPTION|ANSWER|CODE|PUNCH|BRAND|RESPONSE)S?"
                   rf"\s+(?:SELECTED|CHOSEN|PICKED)\s+(?:AT|IN)\s+(?P<source>{_QID})", re.I),
        "not_selected",
    ),
    (
        # The forms a real document actually writes: a NOUN sits between the
        # verb and the word that matters — "SHOW ONLY BRANDS SELECTED AT Q4A",
        # "ONLY OF PHARMACIES USED AT Q4A", "SHOW ONLY THOSE SELECTED IN Q13" —
        # and USED / MENTIONED mean SELECTED. None of them matched, so a grid
        # meant to show the pharmacies someone uses showed every pharmacy, and
        # the data carried ratings from people who had never used the brand.
        # Placed after the NOT form, and refusing any phrase containing NOT, so
        # an exclusion can never be read as an inclusion.
        re.compile(
            rf"(?:ONLY\s+)?(?:DISPLAY|SHOW|PIPE|INSERT|ASK)?\s*(?:ONLY\s+)?(?:OF\s+)?"
            rf"(?!(?:(?!\b(?:SELECTED|USED|MENTIONED|CHOSEN|PICKED)\b).)*\bNOT\b)"
            rf"(?:[A-Za-z][A-Za-z ]{{0,28}}?\s+)?"
            rf"\b(?:SELECTED|USED|MENTIONED|CHOSEN|PICKED)\b\s+(?:AT|IN|FROM)\s+"
            rf"(?P<source>{_QID})",
            re.I,
        ),
        "selected",
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
_PIPE_QID_RE = re.compile(
    rf"(?:INSERT|PIPE)\s*:?\s*(?:RESPONSE\s+(?:FROM|FOR)\s+)?(?P<source>{_QID})\b"
    # "<<Q6>>", "{B2}", "«MAIN_BANK»", "[Q3 answer]": the marker IS the pipe.
    rf"|(?:<<|<|\{{|«)\s*(?P<marked>[A-Za-z]{{1,3}}\d+[a-z]?)\s*(?:>>|>|\}}|»)"
    rf"|\[\s*(?P<named>[A-Za-z]{{1,3}}\d+[a-z]?)\s+(?:ANSWER|RESPONSE|SELECTION)\s*\]",
    re.I,
)
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

# "[INSERT INTERNET PROVIDER SELECTED AT S5a]", "Pipe in primary provider brand
# selected or auto-punched at S5a": ONE answer put into the wording. Only a
# plural ("BRANDS SELECTED AT", "CODES CHOSEN AT") builds an answer LIST — read
# as a mask, every question that merely names the S5a provider had its own
# answers replaced by S5a's.
_PIPE_SELECTED_RE = re.compile(
    rf"(?:\bINSERT|\bPIPE\s+IN)\s+(?P<what>(?:(?!\b(?:BRANDS|ITEMS|OPTIONS|CODES|STATEMENTS|"
    rf"ANSWERS|RESPONSES|LIST|SERVICES|ALL)\b)[A-Za-z /&'’-]){{0,60}}?)\s*\b(?:SELECTED|CHOSEN|"
    rf"PUNCHED)(?:\s+OR\s+AUTO-?PUNCHED)?\s+(?:AT|IN|FROM)\s+(?P<source>{_QID})",
    re.I,
)
# "Ask if more than one response is selected at S5": who is asked, not a list.
_ASKING = re.compile(r"\b(?:ASK|SHOW)\s+(?:ONLY\s+)?(?:IF|FOR)\b|\bIF\b[^.]{0,40}$", re.I)


def _not_a_mask(match: re.Match, fragment: str, pipes: list[re.Match]) -> bool:
    """A "selected at" that is a pipe or a condition, not a list of answers."""
    if any(p.start() <= match.end() and match.start() <= p.end() for p in pipes):
        return True
    return bool(_ASKING.search(fragment[: match.start() + 12]))


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

        singular = list(_PIPE_SELECTED_RE.finditer(fragment))
        for match in singular:
            source = match.group("source").upper()
            if (source, None) not in seen_pipes:
                seen_pipes.add((source, None))
                pipes.append(Pipe(source_qid=source, name=None, verbatim=match.group(0).strip()))
        for pattern, mode in _MASK_PATTERNS:
            match = pattern.search(fragment)
            if not match:
                continue
            if mode in ("selected", "not_selected") and _not_a_mask(match, fragment, singular):
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
            source = (match.group("source") or match.group("marked") or match.group("named")).upper()  # noqa: E501
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
