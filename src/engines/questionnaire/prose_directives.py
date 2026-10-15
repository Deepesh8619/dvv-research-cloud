"""Read the scripter directives in a PROSE questionnaire, without an LLM.

Prose questionnaires have no table structure, so the spec-table parser does
nothing for them — and two of the three real client documents are prose. But
they are not unstructured. The conventions that carry the fields a reviewer
actually needs are bracketed and unambiguous:

    S2.  Do you, or does anyone in your household, work in any of these?
    Select all that apply.                      <- the TYPE
    [PN: RANDOMIZE]                             <- a scripter directive
    Advertising or market research [TERMINATE]  <- a screen-out, per option
    Healthcare
    None of these [ANCHOR, EXCLUSIVE]           <- option flags

Measured across both prose client documents: bracketed programmer notes appear
on 53% and 40% of questions, RANDOMIZE on 38% and 44%, an explicit type
instruction on 30% and 25%. Where these appear they are exact — a `[TERMINATE]`
tag is not a judgement call.

An earlier pass concluded prose "cannot be parsed deterministically". That was
wrong, and wrong for an avoidable reason: it searched for "please select
one/all" and numbered option lines, neither of which these documents use. The
signals were there; the patterns were not.

Same conservative contract as spec_table.py: anything uncertain is left None
and named in `missing`, so the caller escalates to a model. A wrong
deterministic answer is silent and looks authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.survey_definition import Expression, Option, QuestionType
from src.engines.questionnaire.learned import (
    LearnedPattern,
    learned_hidden_variable,
    learned_option_tags,
    learned_randomize,
    learned_screen_out_range,
    learned_type,
)
from src.engines.questionnaire.routing_prose import parse_routing
from src.engines.questionnaire.select_limits import read_limits
from src.intake.questionnaire_loader import (
    bracket_bodies,
    is_directive_only,
    strip_brackets,
)

# A leading question label: "S2.", "Q12a)", "P1:" — the segmenter already found
# it, but the text still carries it and it is not part of the wording.
_LABEL_RE = re.compile(r"^\s*([A-Za-z]{0,3}\d+[a-z]?)\s*[.):]\s*")

# A whole line that is only scripter directives. Real questionnaires put
# SEVERAL on one line — "[SP] [IF D2 = 1]", "[IF S2=1] [MP] [RANDOMIZE a-f]" —
# and they nest. Reading them by bracket depth (`is_directive_only` /
# `bracket_bodies`) is what keeps a directive line out of the answer list.

# A directive too long for one line wraps mid-bracket:
#
#     [ALLOW RESPONDENT TO EITHER TYPE ANSWER IN NUMBER BOX OR CHECK
#     DON'T KNOW, PROMPT WITH THE FOLLOWING...]
#
# Each wrapped line has no closing bracket of its own, so it read as an answer
# option and the question grew three options nobody could ever pick. Joined
# back together before parsing, bounded so an unmatched stray "[" in respondent
# wording cannot swallow the rest of the question.
_MAX_WRAPPED_DIRECTIVE_LINES = 6


def _join_wrapped_directives(lines: list[str]) -> list[str]:
    """Join lines that continue an unclosed bracketed directive."""
    joined: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        span = 1
        while (
            line.count("[") > line.count("]")
            and i + span < len(lines)
            and span < _MAX_WRAPPED_DIRECTIVE_LINES
        ):
            line = f"{line} {lines[i + span]}"
            span += 1
        if line.count("[") > line.count("]"):
            # Never closed — not a directive after all; keep the lines as they
            # were written rather than merging unrelated content.
            joined.append(lines[i])
            i += 1
            continue
        joined.append(line)
        i += span
    return joined


# A question stem that wraps onto the next line is still the stem, not option 1.
# PDFs deliver one line per block, so every wrapped question was silently
# donating its tail to the answer list (and shifting every option code by one).
# The stem is continuing while it has not yet reached its terminal punctuation
# and no option has been seen.
_STEM_CONTINUES_RE = re.compile(r"[?:.!\]]\s*$")

# The code an option line writes for itself: "5. Much better off", "98) None".
# Positional numbering is only right when the author left the codes implicit —
# scales are routinely written in descending order (5,4,3,2,1) and skip codes
# entirely (1,2,3,98,99), so counting down the list invents codes that the data
# file does not use and every one of them becomes a false discrepancy.
_OPTION_CODE_RE = re.compile(r"^\s*(\d{1,3})\s*[.):]\s+(?=\S)")
# Multi-punch items are as often lettered as numbered ("a. It's cheaper").
# The letter is an item marker, not a code — strip it from the label, keep the
# position as the code, so the label matches the data map's option label.
_OPTION_LETTER_RE = re.compile(r"^\s*([A-Za-z])\s*[.):]\s+(?=\S)")

# A trailing tag on an option: "Healthcare [TERMINATE]", "None [ANCHOR, EXCLUSIVE]".
# EVERY trailing bracket group, not just the last: "None of the above [ANCHOR]
# [EXCLUSIVE] [TERMINATE]" is three separate tags on one option, and reading
# only [TERMINATE] left the anchor and the exclusivity in the label text where
# nothing acts on them (and put the brackets in front of the respondent).
# A tag body never contains another "[". Without that, one UNCLOSED bracket
# swallows the question: "Q21d.[ASK Q21e IF USED VIVO AT Q13 (Q13.7=1-5) And
# where do you feel VIVOHealth Pharmacy falls short? [OPEN END]" has no "]"
# until the end, so "[^\]]*" ran from "[ASK" all the way to "END]" and the
# whole sentence was stripped as if it were one scripter tag. The wording
# went to nothing and the question was stored as display text — never asked,
# in a document a client had signed off. Refusing to cross a "[" leaves the
# unparseable directive visible in the wording, which is this module's rule
# already: a directive nobody noticed is worse than an untidy one.
_OPTION_TAG_RE = re.compile(r"\s*(?P<tags>\[[^\][]*\](?:\s*\[[^\][]*\])*)\s*$")

# Type instructions, most specific first — "select all that apply" must beat a
# bare "select".
# Built from an inventory of EVERY bracketed directive across the three real
# client documents — 116 distinct shapes. Numeric forms are checked before the
# open-text ones, because "NUMERIC OE" and "OE" both contain "OE".
_TYPE_PATTERNS: list[tuple[re.Pattern, QuestionType]] = [
    (re.compile(r"select all that apply|choose all that apply|multiple answers",
                re.I), QuestionType.MULTI),
    (re.compile(r"select one|choose one|single answer|select your", re.I),
     QuestionType.SINGLE),
    # NUMERIC first: "NUMERIC OE: 3-DIGIT MINIMUM" is a number, not a verbatim.
    (re.compile(r"numeric\s*oe|record number|enter (a )?number|\bnumeric\b|"
                r"\byears\b|\bage\b.*\bnumber\b|number\s+\d+\s*-\s*\d+|"
                r"\d+\s*-\s*digit|num(?:ber)?\s*box", re.I),
     QuestionType.NUMERIC),
    # "Please rank from 1 to 8, with 1 being the most influential".
    (re.compile(r"\brank\b.{0,20}\b(top|first|three|order)\b|\brank \d[, \d]*and\b|"
                r"^\s*rank\b|\bplease rank\b|\brank\b.{0,30}\bfrom 1 to \d+", re.I),
     QuestionType.RANK),
    # TEXTBOX (11x) and "OE: n-CHARACTER MINIMUM" (6x) are the commonest
    # open-text declarations in these documents and were both being missed.
    (re.compile(r"_\s*_\s*_|open[- ]?end|\btextbox\b|\boe\b|please describe|"
                r"in your own words|type your|enter response|verbatim", re.I),
     QuestionType.OPEN_TEXT),
]

# SP / MP are the two most universal punch abbreviations in survey scripting —
# every panel and platform writes them, and no client document has to teach us
# what they mean. They are checked ONLY inside a bracketed directive: "SP" and
# "MP" are two-letter tokens that appear innocently in ordinary prose and in
# brand names, so searching them against the whole question text would invent
# types. Tried after `_TYPE_PATTERNS` so "[NUMBER BOX WITH SP]" stays numeric.
_PUNCH_DIRECTIVE_PATTERNS: list[tuple[re.Pattern, QuestionType]] = [
    (re.compile(r"\bMP\b|\bmulti(?:ple)?\s*(?:punch|select)\b|\bcheck\s*all\b", re.I),
     QuestionType.MULTI),
    (re.compile(r"\bSP\b|\bsingle\s*(?:punch|select)\b|\bradio\b", re.I),
     QuestionType.SINGLE),
    (re.compile(r"\bTXT\b", re.I), QuestionType.OPEN_TEXT),
]

# The option list is not written out here — it is taken from another question
# ("[SHOW Q26 LIST. RANDOMIZE]", "[INSERT RESPONSES FROM Q4]"). The type
# instruction is still stated and still trustworthy; only the options are absent.
_BORROWED_OPTIONS_RE = re.compile(
    r"\bSHOW\s+[A-Z]{0,3}\d+[A-Z]?\s+LIST\b|"
    r"\bINSERT\s+.{0,40}?\bFROM\s+[A-Z]{0,3}\d+", re.I
)

# A directive naming ROWS or STATEMENTS is describing a grid: the option list
# is the row set, not the answer set. ("SHOW ONLY ITEMS SELECTED AT Q18 IN
# ROWS", "RANDOMIZE STATEMENTS".) Unlike "each of the following", which is
# suggestive prose, this is the scripter stating the structure outright.
_GRID_DIRECTIVE_RE = re.compile(r"\bIN ROWS\b|\bROWS\b|\bSTATEMENTS\b", re.I)

# Tags carried by an individual option.
_BARE_TERMINATE_RE = re.compile(r"\s(TERMINATE|SCREEN\s*-?\s*OUT)\s*$")
_TERMINATE_TAG = re.compile(r"\bTERMINATE\b|\bSCREEN\s*-?\s*OUT\b", re.I)
_EXCLUSIVE_TAG = re.compile(r"\bEXCLUSIVE\b", re.I)
_ANCHOR_TAG = re.compile(r"\bANCHOR\b", re.I)
_ENTRY_PROMPT = re.compile(r"^\s*(?:Fill\s+in|Enter|Record)\b", re.I)
# "[0-100]", "[0-1,000]" — a pure number range, nothing else in the bracket.
_NUMBER_RANGE = re.compile(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)")
# "[0-1000, WHOLE NUMBER]", "[0-100 RESPONSE, WHOLE NUMBER]", "[0-100%]": the
# range plus how to type it. Only these words — "[0-100, MUST SUM TO 100%]"
# is a total, not a single number.
_NUMBER_RANGE_STATED = re.compile(
    r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s*%?\s*(?:RESPONSE)?\s*"
    r"(?:,?\s*(?:WHOLE\s+NUMBERS?|INTEGERS?|NUMERIC|NUMBERS?\s+ONLY))?\s*",
    re.I,
)
# Empty table cells at either end of a serialized row: "label |  | ".
_EMPTY_CELLS = re.compile(r"^(?:\s*\|)+\s*|(?:\s*\|)+\s*$")

# "CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS" — the one range-screening
# wording common enough across client documents to read without teaching.
# Anything else needs a founder to teach it (screen_out_range in learned.py).
_CONTINUE_IF_RE = re.compile(r"\bCONTINUE\s+IF\b", re.I)
_TERMINATE_ALL_OTHERS_RE = re.compile(r"\bTERMINATE\s+ALL\s+OTHERS\b", re.I)
_RANGE_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,3})")


def _range_terminate(
    body: str, qid: str, patterns: list[LearnedPattern] | None
) -> tuple[Expression, str] | None:
    """A document-level screen-out stated as a range, not per-option codes.

    Two ways in: the built-in "CONTINUE IF ... N-M ... TERMINATE ALL OTHERS"
    wording (no teaching needed), or a founder-taught screen_out_range phrase
    with the range read right after it. Either way the result is the same
    shape: terminate if the answer is below the low bound OR above the high
    one — never a per-option code, since this question has none.

    Returns (expression, evidence) or None; evidence names the built-in match
    or the taught phrase that found it, so a reviewer can see where it came
    from without expanding the expression.
    """
    if _CONTINUE_IF_RE.search(body) and _TERMINATE_ALL_OTHERS_RE.search(body):
        match = _RANGE_RE.search(body)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if low <= high:
                return _range_expression(qid, low, high, body), body
    taught = learned_screen_out_range(patterns, body)
    if taught is not None:
        low, high, matched = taught
        return _range_expression(qid, low, high, body), f"learned: {matched}"
    return None


def _range_expression(qid: str, low: int, high: int, verbatim: str) -> Expression:
    return Expression(
        op="or",
        children=[
            Expression(op="lt", qid=qid, value=low),
            Expression(op="gt", qid=qid, value=high),
        ],
        verbatim_text=verbatim,
    )

# A scale row: "0=Not Important At All | 1 | 2 | ... | 10=Very Important".
# Pipe-separated points above a statement list is how these documents write a
# rating grid; the statements below are the rows, not the answer options.
_SCALE_ROW_RE = re.compile(r"^[^|]+(\|[^|]+){3,}$")

# A section banner — "***** END OF RENT SECTION *****". It divides the
# document; it is not an answer anybody can pick. Left in, it became option 1
# of whatever numeric question happened to end the section.
_SECTION_BANNER_RE = re.compile(r"^\s*\*{3,}")

# The rule the respondent writes on: "_____", "$ ______", "____ %", or the same
# alongside the widget directive that describes it ("[ Num box, 0-25]  ___").
# It marks WHERE the answer goes, so reading it as an answer gave every
# number-box question a phantom option and made the data map's "this column is
# an open response" look like a disagreement.
_ANSWER_BLANK_RE = re.compile(r"^[\s$%()·.\u2013\u2014_-]*$")


def _is_answer_blank(line: str) -> bool:
    without_directives = strip_brackets(line)
    return "_" in without_directives and bool(
        _ANSWER_BLANK_RE.match(without_directives)
    )


# A wording that opens with an all-caps CODE/RECODE instruction is addressed to
# the scripter, not the respondent: "CODE AGE - BASED ON RESPONSES AT S1...".
# These are derived variables computed from other answers, so they have no type
# to find and must not be counted as a question the model still has to resolve.
# "CODE AGE", "RECODE REGION" — and "BUBBLE AUDIENCE CODE", where the verb
# comes last. Anywhere in the line: the all-caps test below is what keeps a
# respondent's question out.
# "SAMPLE SPLIT", "RANDOMLY ASSIGN": the scripter assigns it — nobody is asked.
# A note that declares the variable outright, rather than leaving it to be
# inferred from shouting. "[PN: HIDDEN VARIABLE]", "[HV]", "[NOT ASKED —
# COMPUTED]". `HIDDEN` alone is deliberately enough inside a bracket: nothing
# else in a scripter directive says it.
_STATED_HIDDEN = re.compile(
    r"\bhidden\b|\bHV\b|\bnot\s+asked\b|\bderived\s+variable\b", re.I
)

_DERIVED_RE = re.compile(
    r"(?<!ZIP )(?<!POSTAL )(?<!AREA )(?<!PROMO )\b(RE)?CODE\b|\bSPLIT\b|\bRANDOMLY ASSIGN"
)


# Wordings that cannot be multi-select whatever the scripter left in the type
# cell. A superlative ("best describes") admits exactly one answer by
# construction, and "what is your <fact>" asks for one value. Deliberately does
# NOT include "how often" or "how would you rate": those are commonly grids in
# these documents, and guessing single there would be silently wrong.
_SINGLE_WORDING_RE = re.compile(
    r"\bbest (describes|represents|matches|fits|reflects)\b|"
    r"\bwhich one\b|\bhow involved are you\b|\bwhat is your\b",
    re.I,
)


def is_single_select_wording(text: str) -> bool:
    """True when the wording itself rules out a multi-select answer."""
    return bool(_SINGLE_WORDING_RE.search(text or ""))


def is_derived_wording(text: str, patterns: list[LearnedPattern] | None = None) -> bool:
    """All-caps CODE/RECODE wording = an instruction to the scripter.

    The "no lowercase" test is what keeps this from swallowing a real question
    ("Code the item you bought"): these documents shout scripter instructions
    and speak to respondents in sentence case. Punctuation varies (en dashes,
    ampersands, brackets), so testing case is more portable than enumerating
    the allowed characters — which is how the first version missed most of them.

    A taught `hidden_variable` phrase ("SAMPLE SOURCE", "AUDIENCE CODE")
    overrides this heuristic entirely: a founder naming their client's own
    convention is exact, not a guess, so it applies whatever the casing.
    """
    stripped = text.strip()
    if learned_hidden_variable(patterns, stripped) is not None:
        return True
    # A question mark means somebody is being asked: "WHAT IS YOUR ZIP CODE?"
    # shouted in capitals is still a question, never a computed variable.
    return (
        bool(_DERIVED_RE.search(stripped))
        and not any(c.islower() for c in stripped)
        and "?" not in stripped
    )

# A line that continues the question rather than starting the answer list —
# these read as instructions to the respondent, not as options.
_INSTRUCTION_RE = re.compile(
    r"^(select|choose|please|enter|type)\b", re.IGNORECASE
)

# Routing prose that belongs to the NEXT section, not this question's options
# ("Adult Consumer Path Ages 18-25 (IF S0=1 AND S1=18-25) ASK S3-S6").
# A SECTION routing line names the questions it governs: "Parent Path (IF S0=1)
# ASK P1-P3". The old second alternative matched a bare "IF S11=1" anywhere in a
# line, so an ANSWER OPTION carrying a display condition — "Yes - for a child
# under 18 [ONLY SHOW IF S11=1]" — was read as a section header and thrown away,
# taking a punch code with it. One live study was missing that answer entirely:
# five options in the document, four in the definition, no flag.
_SECTION_ROUTING_RE = re.compile(r"\bASK\s+[A-Z]{0,3}\d", re.I)


# The body of a "[BASE: ...]" tag, as bracket_bodies hands it over (no
# brackets). See questionnaire_loader._BASE_TAG_RE for where these open a
# question rather than annotate one.
_BASE_TAG_BODY = re.compile(r"^\s*BASE\s*[:\-]", re.IGNORECASE)


@dataclass
class ProseFields:
    """What a prose question's own text states. `missing` drives escalation."""

    qid: str
    text: str | None = None
    qtype: QuestionType | None = None
    options: list[Option] = field(default_factory=list)
    terminate_if: Expression | None = None
    terminate_verbatim: str | None = None
    show_if: Expression | None = None
    randomize: bool = False
    # A scripter-computed variable ("CODE AGE - BASED ON S1..."), not a question.
    derived: bool = False
    # Directives we recognised but do not model yet — kept visible, never
    # silently dropped, so an unhandled convention is discoverable.
    directives: list[str] = field(default_factory=list)
    # How many options the respondent may tick: {"min_select", "max_select"}.
    # Empty for the overwhelming majority of questions, which say nothing.
    select_limits: dict[str, int] = field(default_factory=dict)
    # The number range an entry box accepts: {"min", "max"}, from "[0-100]".
    bounds: dict[str, int] = field(default_factory=dict)
    # Sampling targets found in the option list ("TARGET 50/50 GENDER SPLIT").
    # Not answers, and not noise either: they are the quota this study needs.
    quota_hints: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # field -> the exact fragment that decided it. Shown to the reviewer so
    # approving is checking a citation, not trusting a black box.
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing


# A sentence-shaped paragraph: long, punctuated, addressed to the respondent.
# An answer option is a phrase — even a wordy one ("I prefer to buy directly
# from a brand's website") has no sentence break and rarely runs this long.
_PROSE_MIN_WORDS = 18


# "Note:", "PN:", "Programmer note —", "Interviewer note:". Addressed to the
# scripter or the reader, never an answer, however short it is.
_NOTE_PREFIX = re.compile(
    r"^\s*\[?\s*(?:PN|P\.N\.|Programmer\s+Note|Interviewer\s+Note|Note|NB)\s*[:\-\u2013\u2014]",
    re.I,
)
# "e.g.", "i.e.", "etc." and friends end in a period WITHOUT ending a sentence.
# Counting them as sentence breaks made a long, ordinary answer option look like
# prose — which flagged it in the audit and, worse, would have dropped it from
# the option list entirely if it were not the first option.
_ABBREVIATIONS = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|approx|incl|no|Mr|Mrs|Ms|Dr|St|U\.S|U\.K)\.", re.I
)


def is_note_line(line: str) -> bool:
    """A line addressed to the scripter or the reader, not an answer."""
    return bool(_NOTE_PREFIX.match(line.strip()))


def has_sentence_break(text: str) -> bool:
    """True when the text really ends or joins sentences, abbreviations aside."""
    without = _ABBREVIATIONS.sub(" ", text)
    return bool(re.search(r"[.!?](?:\s+[\"\'(\[]?[A-Z0-9]|\s*$)", without))


# "TARGET 50/50 GENDER SPLIT", "N=400 QUOTA", "MIN 75 / MAX 125". A sampling
# instruction to the field team, written in the option list because that is
# where it is relevant — and read as an answer, it became a punch a respondent
# could pick and a code in the data that means nothing.
_QUOTA_INSTRUCTION = re.compile(
    r"\b(?:TARGET|QUOTA|SOFT\s+LAUNCH|MIN\s*N|MAX\s*N|N\s*=\s*\d+)\b"
    r"|\b\d+\s*/\s*\d+\b(?=[^a-z]*$)",
    re.I,
)
# "MONITOR S6 & S7 FALLOUT FOR CONSISTENCY WITH WAVE 1:" — an instruction to the
# team, sitting under the answers because that is what it is about. Read as an
# answer it became a punch a respondent could choose. A quota target was only
# the first of these; the verb changes, the shape does not.
_INSTRUCTION_VERB = re.compile(
    r"^(?:MONITOR|CHECK|CONFIRM|VERIFY|REVIEW|FLAG|TRACK|COMPARE|ENSURE|NOTE|"
    r"RECORD|CODE|CAPTURE)\b",
    re.I,
)


# A header a scripter writes over a section: capitals, several words, and the
# condition that routes people into it — "CONSUMER PANEL ... & SCREENER (S0=1)".
_SECTION_HEADER_RE = re.compile(
    r"^[^a-z]{12,}\(\s*[A-Z][A-Za-z0-9_.]*\s*=\s*[\d][\d,\s\-]*\)\s*$"
)


def is_section_header(line: str) -> bool:
    """True for an all-caps section title carrying its entry condition."""
    return bool(_SECTION_HEADER_RE.match(strip_brackets(line or "").strip()))


def is_quota_instruction(line: str) -> bool:
    """A sampling target, not an answer.

    Requires SHOUTING as well as the keyword: a scripter writes an instruction
    in capitals, and an ordinary answer that happens to contain the word
    "target" ("I target a budget each month") is not one.
    """
    text = strip_brackets(line or "").strip()
    if not text or any(c.islower() for c in text):
        return False
    return bool(_QUOTA_INSTRUCTION.search(text) or _INSTRUCTION_VERB.match(text))


# Answers that close a list: a respondent reads them last wherever the document
# happened to type them, and a list that offers "None of these" in the middle
# reads as a mistake and collects worse data. Universal across every client, and
# never randomised into the body of the list.
_TERMINAL_OPTION = re.compile(
    # An optional "I" in front: a respondent reads "I don't know" and "Don't
    # know" as the same closing answer, and anchoring on the phrase alone left
    # the first one sitting in the middle of a list of health systems.
    r"^\s*(?:i\s+|i'?m\s+)?"
    r"(?:other|none|no,?\s|not\s+sure|don'?t\s+know|do\s+not\s+know|"
    r"prefer\s+not|refuse|n/?a\b|all\s+of\s+(?:these|the\s+above)|"
    r"none\s+of\s+(?:these|the\s+above))",
    re.I,
)


def is_terminal_option(label: str) -> bool:
    """An answer that closes a list rather than sitting inside it."""
    return bool(_TERMINAL_OPTION.match((label or "").strip()))


def order_terminal_options(options: list[Option]) -> list[Option]:
    """Move the closing answers to the end and pin them there.

    Codes are NEVER touched — the data is keyed on them, and reordering the
    display must not renumber a single punch.
    """
    body = [o for o in options if not is_terminal_option(o.label)]
    closing = [o for o in options if is_terminal_option(o.label)]
    if not closing or not body:
        return options
    for option in closing:
        option.anchored = True
    return [*body, *closing]


def _strip_bracket(text: str, body: str) -> str:
    """Remove the one bracket holding `body`, and tidy the space it leaves."""
    out = text.replace(f"[{body}]", " ", 1)
    return " ".join(out.split()).strip()


def _is_prose_paragraph(line: str) -> bool:
    text = strip_brackets(line).strip()
    if _OPTION_CODE_RE.match(text) or _OPTION_LETTER_RE.match(text):
        return False
    if not any(c.islower() for c in text):
        return False  # all-caps is a scripter instruction, handled elsewhere
    words = text.split()
    if len(words) < _PROSE_MIN_WORDS:
        return False
    return has_sentence_break(text)


def _strip_label(line: str) -> str:
    return _LABEL_RE.sub("", line, count=1).strip()


def _is_binary_choice(options: list[Option]) -> bool:
    """Exactly Yes/No — the one option shape whose type is not a judgement call.

    `_infer_type` deliberately refuses to guess single vs multi from options
    alone, because that choice changes the stored data structure. A two-option
    yes/no list is the documented exception: it cannot be multi-select in any
    questionnaire convention, and it is the commonest shape in these documents'
    screener sections.
    """
    if len(options) != 2:
        return False
    labels = {re.sub(r"[^a-z]", "", o.label.lower()) for o in options}
    return labels in ({"yes", "no"}, {"yesnoperm"}) or labels == {"no", "yes"}


def _infer_type(
    text: str,
    options: list[Option],
    directives: list[str] | None = None,
    borrowed_options: bool = False,
    patterns: list[LearnedPattern] | None = None,
) -> tuple[QuestionType | None, str]:
    """Type from the instruction line OR a scripter directive, plus its evidence.

    Measured across all three real client documents, an unstated type was the
    single biggest reason a question still needed a model: missing on 63, 57
    and 56 questions. The reason was avoidable — the declaration is often
    inside the bracketed note rather than on the instruction line:

        [PN: OPEN END]                        -> open text
        [ENTER NUMBER 1-100]                  -> numeric
        [PN: OPEN END. ALLOW UP TO 10 RESPONSES]

    Reading both halves is what turns those into free answers.

    Returns the fragment that decided it as well as the type, so the review UI
    can cite the source instead of asking a reviewer to take the answer on
    faith. An empty string means nothing decided it.
    """

    def _blocked(qtype: QuestionType) -> bool:
        # "Select all that apply" with no options is a contradiction; trust an
        # instruction only when the shape agrees with it, or when the options
        # are explicitly borrowed from another question.
        return (
            qtype in (QuestionType.MULTI, QuestionType.SINGLE)
            and not options
            and not borrowed_options
        )

    # Taught patterns first. A founder binding "[SHOWCARD]" to single is
    # telling us something the built-in table cannot know — their client's
    # convention — so it outranks our guesses rather than losing to them.
    taught = learned_type(patterns, *(directives or []), text)
    if taught is not None:
        return taught

    # Widget directives first, across every directive, THEN the punch
    # abbreviations. A number-box question states both — "[SP]" describes how
    # its special codes ("888. Still enrolled") are punched, not what the
    # question collects — and whichever was written first was winning.
    for patterns in (_TYPE_PATTERNS, _PUNCH_DIRECTIVE_PATTERNS):
        for directive in directives or []:
            for pattern, qtype in patterns:
                match = pattern.search(directive)
                if match and not _blocked(qtype):
                    return qtype, f"[{directive}]"

    for pattern, qtype in _TYPE_PATTERNS:
        match = pattern.search(text)
        if match and not _blocked(qtype):
            return qtype, match.group(0)

    if any(o.exclusive for o in options) and not _blocked(QuestionType.MULTI):
        # An option tagged EXCLUSIVE only means something in a multi-punch: in a
        # single-select every option is already exclusive, so nobody writes it.
        # Without this S6 was typed single and its list answer went into one
        # column as NULL — the punches silently vanished from the data file.
        return QuestionType.MULTI, "an option tagged EXCLUSIVE"

    if _is_binary_choice(options):
        return QuestionType.SINGLE, "a Yes/No option pair"
    if options and is_single_select_wording(text):
        return QuestionType.SINGLE, "wording that admits only one answer"
    # Options but no instruction: single vs multi changes the data structure,
    # so this is a real question for a model, not a guess.
    return None, ""


# "1Acne or breakouts", "2Redness or rosacea" — a numbered answer list whose
# separator did not survive whatever produced the document. Found on two real
# questions, and the respondent was being shown the digit: the code pattern
# needs a separator and a space, so nothing stripped it and the label kept it.
# An UPPER-case letter after the digit, which is what a mangled code leaves
# behind. A label that genuinely opens with a number carries on in lower case —
# "1st choice", "2x per week", "1 in 5" — and splitting those produced the
# labels "st choice" and "x per week", with the first option lost entirely.
_GLUED_CODE_RE = re.compile(r"^(?P<code>\d{1,3})(?=[A-Z])")


def _adopt_glued_codes(options: list[Option], written_codes: list[int | None]) -> None:
    """Split "1Acne" into code 1 and label "Acne" — but only for a whole list.

    Never per option, because "10 or more" is a perfectly ordinary label and
    splitting it alone yields code 1 and the label "0 or more". The split is
    taken only when EVERY option starts with a glued digit AND those digits run
    consecutively from the first one, which an answer list does and a list of
    quantities does not.
    """
    if len(options) < 2 or any(c is not None for c in written_codes):
        return
    found = [_GLUED_CODE_RE.match(o.label or "") for o in options]
    if not all(found):
        return
    codes = [int(m.group("code")) for m in found]
    if codes != list(range(codes[0], codes[0] + len(codes))):
        return
    for option, match, code in zip(options, found, codes, strict=True):
        option.label = (option.label or "")[match.end() :].strip()
        written_codes[options.index(option)] = code


def _parallel_to_next(line: str, body_lines: list[str], position: int) -> bool:
    """Is this line the FIRST of a list rather than the tail of a wrapped stem?

    A stem that wraps does not look like the line under it; the first item of a
    list looks exactly like the second. Compared on the leading character class
    alone, which is all that distinguishes them reliably:

        Rank these          <- stem
        1st choice          <- absorbed into the stem, and lost
        2nd choice

    Without this the option list starts one item short, and the missing answer
    is sitting in the question's own wording where nobody reads it as an answer.
    """
    following = next(
        (
            nxt
            for nxt in body_lines[position + 1 :]
            if nxt.strip() and not is_directive_only(nxt)
        ),
        None,
    )
    if following is None:
        return False
    return (line[:1].isdigit() and following[:1].isdigit()) or (
        line[:1].isalpha()
        and following[:1].isalpha()
        and line[:1].isupper() == following[:1].isupper()
        and len(line) < 40
        and len(following) < 40
    )


def parse_prose_question(
    chunk_text: str,
    candidate_qid: str | None,
    patterns: list[LearnedPattern] | None = None,
) -> ProseFields | None:
    """Extract what a prose question states. None if it has no usable id."""
    lines = [ln.replace("\t", " ").strip() for ln in (chunk_text or "").splitlines()]
    lines = _join_wrapped_directives([ln for ln in lines if ln])
    if not lines:
        return None

    # Directives authored ABOVE the label ("[SP] [IF D2 = 1]") govern THIS
    # question — segmentation carries them into this chunk, so they lead the
    # text. The wording is the first line that is not one of them; the rest,
    # before and after, go through the same directive/option pass below.
    head = next(
        (i for i, ln in enumerate(lines) if not is_directive_only(ln)), None
    )
    if head is None:
        return None
    first = lines[head]
    body_lines = lines[:head] + lines[head + 1 :]
    match = _LABEL_RE.match(first)
    qid = (match.group(1) if match else (candidate_qid or "")).strip()
    if not qid:
        return None

    question_text = _strip_label(first)
    # The wording may itself carry a bracketed hint, e.g. "[ENTER NUMBER 1-100]".
    inline_directive = _OPTION_TAG_RE.search(question_text)
    directives: list[str] = []
    if inline_directive:
        directives.extend(bracket_bodies(inline_directive.group("tags")))
        question_text = _OPTION_TAG_RE.sub("", question_text).strip()

    # A base written INSIDE the question-text line — "D3. [ASK IF C1=18+] What
    # is your marital status?" — reached nothing: only directive-ONLY lines were
    # read for routing, so the bracket was dropped entirely, not even kept as a
    # note. On two real studies that silently lost most of the routing: one had
    # twelve questions stating a base and none applied.
    inline_bases = bracket_bodies(question_text)
    instruction_text = question_text
    options: list[Option] = []
    terminate_codes: list[int] = []
    terminate_lines: list[str] = []
    range_terminate: Expression | None = None
    range_terminate_evidence = ""
    show_if: Expression | None = None
    show_if_verbatim = ""
    written_codes: list[int | None] = []
    stem_wraps_used = False
    last_option_marked = False
    randomize = False
    quota_hints: list[str] = []
    intro_follows = False
    randomize_evidence = ""
    scale_row: str | None = None

    for body in inline_bases:
        directives.append(body.strip())
        if _BASE_TAG_BODY.match(body):
            # "[BASE: ALL]" opening the question in an unnumbered document. It
            # is a LABEL, never part of the sentence: leaving it in the wording
            # puts "[BASE: ALL]" on the respondent's screen, and every question
            # in these documents carries one. It stays in `directives` and in
            # source_text, so a base naming a subgroup ("[BASE: THOSE WHO
            # PRESCRIBE SPRAVATO]") is still auditable as a condition nobody
            # applied — see definition_audit.base_stated_in_words_only.
            question_text = _strip_bracket(question_text, body)
            instruction_text = _strip_bracket(instruction_text, body)
            continue
        if show_if is None and not _TERMINATE_TAG.search(body):
            routing = parse_routing(body)
            if routing is not None and qid.upper() not in {
                r.upper() for r in routing.referenced_qids()
            }:
                show_if = routing
                show_if_verbatim = body.strip()
                # Out of the WORDING once it is a condition. The field is what a
                # reviewer reads and approves as the question, and a base left
                # sitting in it is both duplicated — it is in the base field now
                # — and the sort of thing that gets approved as if it were part
                # of the sentence. Only this bracket, only because it parsed;
                # anything we did not understand stays visible, because a
                # directive nobody noticed is worse than an untidy one.
                question_text = _strip_bracket(question_text, body)
                instruction_text = _strip_bracket(instruction_text, body)

    for position, line in enumerate(body_lines):
        if is_directive_only(line):
            for raw in bracket_bodies(line):
                body = raw.strip()
                directives.append(body)
                taught_random = learned_randomize(patterns, body)
                if taught_random is not None:
                    randomize = True
                    randomize_evidence = f"learned: {taught_random}"
                elif re.search(
                    r"RANDOMI[SZ]E|\bROTATE\b", body, re.I
                ) and not re.search(r"KEEP ORDER|DO NOT RANDOMI[SZ]E", body, re.I):
                    randomize = True
                    randomize_evidence = f"[{body}]"
                range_result = _range_terminate(body, qid, patterns)
                if range_result is not None:
                    # A document-level terminate rule stated as a RANGE
                    # ("CONTINUE IF AGES 18-60, TERMINATE ALL OTHERS"), not
                    # per-option codes — built the real expression above rather
                    # than deferring it, since this question has no option list
                    # for a code-based screen-out to attach to.
                    range_terminate, range_terminate_evidence = range_result
                elif _TERMINATE_TAG.search(body):
                    # Stated in prose but not a range we can read (built-in or
                    # taught) — preserve it and let a model resolve it, never
                    # invent an expression.
                    terminate_lines.append(body)
                elif show_if is None:
                    # "[IF S2 = 2 or S2=3]" states this question's base. First
                    # readable one wins; an unreadable one stays a directive.
                    routing = parse_routing(body)
                    if routing is not None and options:
                        # A base stated UNDER the option list governs what comes
                        # next, not this question — "[PN: SHOW IF P1=1]" then an
                        # intro paragraph. Everything below it is that screen's
                        # text; read as options it gave P1 punches nobody could
                        # ever code (and a wrapped paragraph gave it two).
                        intro_follows = True
                    if routing is not None and qid.upper() in {
                        r.upper() for r in routing.referenced_qids()
                    }:
                        # "[PN: SHOW IF P1=1]" under P1's own option list is the
                        # base of what comes NEXT — a question cannot be its own
                        # condition. section_paths turns it into that screen.
                        routing = None
                    if routing is not None:
                        show_if = routing
                        show_if_verbatim = body
            continue

        if _SECTION_ROUTING_RE.search(line):
            # Belongs to the next section's routing, not this option list.
            directives.append(line)
            continue

        if _SCALE_ROW_RE.match(line):
            # The scale points define a rating grid; the lines after it are the
            # statements being rated. Record the scale, do not treat the points
            # as this question's answer options.
            directives.append(line)
            scale_row = line
            continue

        if _SECTION_BANNER_RE.match(line):
            directives.append(line.strip())
            continue

        if _is_answer_blank(line):
            # Keep the widget directive it carries ("[Num box 1900-2014]") —
            # that is the type declaration — and drop the rule itself.
            directives.extend(b.strip() for b in bracket_bodies(line))
            continue

        if _INSTRUCTION_RE.match(line) and not options:
            instruction_text = f"{instruction_text} {line}"
            continue

        if (
            not options
            and not stem_wraps_used
            and not _OPTION_CODE_RE.match(line)
            and not _OPTION_LETTER_RE.match(line)
            and not _STEM_CONTINUES_RE.search(question_text)
            and any(c.islower() for c in question_text)
            and not _parallel_to_next(line, body_lines, position)
        ):
            # The stem wrapped. Finish reading it before the option list starts.
            # ONE line only, and only for a stem that is addressed to the
            # RESPONDENT. An all-caps header is addressed to the scripter — a
            # recode table ("BUBBLE AUDIENCE CODE", "CODE STATE & REGION") also
            # has no terminal punctuation and no option markers, and absorbing
            # its first definition line costs it `derived` on the same
            # no-lowercase rule `is_derived_wording` uses.
            stem_wraps_used = True
            question_text = f"{question_text} {line}".strip()
            instruction_text = f"{instruction_text} {line}".strip()
            continue

        if intro_follows and not (
            _OPTION_CODE_RE.match(line) or _OPTION_LETTER_RE.match(line)
        ):
            directives.append(line.strip())
            continue

        if options and is_section_header(line):
            # "CONSUMER PANEL INTRODUCTION & SCREENER (S0=1)" opens the NEXT
            # section; read as an answer it became option 3 of the sample split.
            # Everything under it is that section's, not this question's.
            directives.append(line.strip())
            intro_follows = True
            continue

        if options and is_quota_instruction(line):
            # Kept as a quota hint, not dropped: it is how many of each kind the
            # study needs, and somebody has to turn it into a real quota cell.
            quota_hints.append(line.strip())
            directives.append(line.strip())
            continue

        if _ENTRY_PROMPT.match(line):
            # "Fill in [0-100]_____ [%]" is where the respondent types — an
            # instruction about the answer box, never an answer to choose.
            directives.append(line.strip())
            directives.extend(bracket_bodies(line))
            continue

        if is_note_line(line):
            # "Note: Healthcare decisions include researching where to seek
            # care..." sat under S5's options and became answer code 1.
            directives.append(line.strip())
            continue

        if options and _is_prose_paragraph(line):
            # A paragraph is not an answer. The intro to the NEXT screen sits
            # directly under the previous question's option list ("[PN: SHOW IF
            # P1=1]" then "In this survey, we're looking to interview young
            # women ages 13 to 17..."), and reading it as a third option gave P1
            # a code nobody could ever punch and broke the Yes/No pair that
            # decides its type. Kept as a directive so the text is not lost —
            # `section_paths.apply_intro_screens` turns it into its own screen.
            directives.append(line.strip())
            continue

        # Otherwise: an answer option. Codes are positional (1-based), which is
        # how prose lists are scripted.
        # "Male TERMINATE": the same instruction without its brackets. Written
        # in capitals, like every scripter instruction, so an answer that
        # merely uses the word ("I would terminate my plan") is untouched.
        # An answer list written as a one-column table serializes as
        # "Out-of-pocket costs | " — an empty cell beside each answer. The pipe
        # reached respondents, and it hid trailing tags: "Other (specify)
        # [ANCHOR] |" kept its bracket on screen and was never anchored.
        line = _EMPTY_CELLS.sub("", line)
        bare = _BARE_TERMINATE_RE.search(line)
        if bare and not _OPTION_TAG_RE.search(line):
            line = f"{line[: bare.start()].rstrip()} [{bare.group(1)}]"
        tags = _OPTION_TAG_RE.search(line)
        label = _OPTION_TAG_RE.sub("", line).strip() if tags else line
        written = _OPTION_CODE_RE.match(label)
        lettered = None if written else _OPTION_LETTER_RE.match(label)
        marker = written or lettered
        if marker:
            label = label[marker.end() :].strip()
        if not label:
            continue

        # An option's wording wraps too. A marker-less line following a MARKED
        # option is the rest of that option, not a new one — SHED's S2 wraps
        # each of its three housing answers, and reading the tails as answers
        # turned a 3-code question into a 6-code one whose codes matched
        # nothing in the data. Only one continuation line, and only after a
        # marked option, so a list written entirely without markers is
        # untouched.
        continuation = bool(options) and last_option_marked and marker is None
        if continuation:
            option = options[-1]
            option.label = f"{option.label} {label}".strip()
            last_option_marked = False
        else:
            written_codes.append(int(written.group(1)) if written else None)
            option = Option(code=len(options) + 1, label=label)
            last_option_marked = marker is not None

        if tags:
            tag_text = " ".join(bracket_bodies(tags.group("tags")))
            taught_meanings = learned_option_tags(patterns, tag_text)
            if _EXCLUSIVE_TAG.search(tag_text) or "exclusive" in taught_meanings:
                option.exclusive = True
            if _ANCHOR_TAG.search(tag_text) or "anchor" in taught_meanings:
                option.anchored = True
            if _TERMINATE_TAG.search(tag_text) or "terminate" in taught_meanings:
                terminate_codes.append(option.code)
                terminate_lines.append(f"{option.label} [{tag_text.strip()}]")
            else:
                # A condition on this ANSWER: "Yes - for a child under 18 [ONLY
                # SHOW IF S11=1]". Modelled nowhere until now, so the option was
                # offered to everyone the script meant to exclude.
                per_option = parse_routing(tag_text)
                if per_option is not None:
                    option.show_if = per_option
        if not continuation:
            options.append(option)

    # Adopt the written codes only when EVERY option states one and they are
    # distinct. A partial list (wrapped option text, an unnumbered "Other")
    # keeps positional numbering rather than mixing two schemes.
    _adopt_glued_codes(options, written_codes)
    if (
        options
        and len(written_codes) == len(options)
        and all(c is not None for c in written_codes)
        and len(set(written_codes)) == len(written_codes)
    ):
        remap = {opt.code: wc for opt, wc in zip(options, written_codes, strict=True)}
        for opt, wc in zip(options, written_codes, strict=True):
            opt.code = wc
        terminate_codes = [remap.get(c, c) for c in terminate_codes]

    fields = ProseFields(
        qid=qid,
        text=question_text or None,
        options=options,
        randomize=randomize,
        directives=directives,
        quota_hints=quota_hints,
    )
    if show_if is not None:
        fields.show_if = show_if
        fields.evidence["base"] = f"[{show_if_verbatim}]"
    if randomize_evidence:
        fields.evidence["randomize"] = randomize_evidence
    # A scripter note that SAYS SO: "[PN: HIDDEN VARIABLE. COMPUTE FROM S3]".
    # A stated fact outranks every wording heuristic, and it is the only signal
    # that works for a variable whose name gives nothing away — "AGE" is a
    # question in one study and a hidden recode in the next, and the only thing
    # that distinguishes them is the note beside it.
    stated_hidden = next(
        (d for d in directives if _STATED_HIDDEN.search(d)), None
    )
    taught_hidden = question_text and learned_hidden_variable(patterns, question_text)
    if stated_hidden:
        fields.derived = True
        fields.qtype = QuestionType.INFO
        fields.evidence["derived"] = f"[{stated_hidden}]"
    elif question_text and (taught_hidden or is_derived_wording(question_text, patterns)):
        # Scripter-computed, not asked. Labelled the same way the extractor
        # labels a HID_ variable, so the review UI says "needs manual setup".
        fields.derived = True
        fields.qtype = QuestionType.INFO
        fields.evidence["type"] = (
            f"learned: {taught_hidden}" if taught_hidden
            else "an all-caps CODE instruction to the scripter"
        )
    elif declared_free_text := next(
        (
            (qtype, directive)
            for directive in directives
            for pattern, qtype in _TYPE_PATTERNS
            if qtype in (QuestionType.OPEN_TEXT, QuestionType.NUMERIC)
            and pattern.search(directive)
        ),
        None,
    ):
        # The question's OWN directive outranks a shape found in the lines
        # below it. Q21d says "[OPEN END]" and its chunk then runs on into a
        # section header, a [PLACEHOLDER] table of campaign creative, a
        # "PN. CODE POSITION EXPOSURE" note and a quota grid — and the quota
        # grid's "n=166-167 | n=166-167 | n=166-167" reads as a scale row, so
        # a verbatim was typed as a grid on the strength of another section's
        # furniture.
        #
        # A question that states it collects typed text or a number cannot be
        # a grid, whatever appears underneath it. The chunk boundary is still
        # wrong — that is a separate defect — but a stated type is not a thing
        # to overturn on a guess.
        fields.qtype, deciding_directive = declared_free_text
        fields.evidence["type"] = f"[{deciding_directive}]"
    elif scale_row:
        fields.qtype = QuestionType.GRID_SINGLE
        fields.evidence["type"] = scale_row
    elif options and (
        grid_directive := next(
            (d for d in directives if _GRID_DIRECTIVE_RE.search(d)), None
        )
    ):
        fields.qtype = QuestionType.GRID_SINGLE
        fields.evidence["type"] = f"[{grid_directive}]"
    else:
        fields.qtype, type_evidence = _infer_type(
            instruction_text,
            options,
            directives,
            borrowed_options=any(_BORROWED_OPTIONS_RE.search(d) for d in directives),
            patterns=patterns,
        )
        if type_evidence:
            fields.evidence["type"] = type_evidence

    # "Select up to 2." A cap is a fact about the ANSWER, and it also settles
    # the type: a question that lets somebody tick two is a multi-punch, and a
    # cap of one is a single. Read after the type so an explicit declaration
    # ("[SP]") still wins, but before escalation, so a capped question does not
    # cost a model call to type something the sentence already says.
    limits = read_limits(instruction_text)
    if limits:
        fields.select_limits = limits
        ceiling = limits.get("max_select")
        # "Which best describes … Select up to 2": "best describes" is a guess
        # from wording; the stated cap is a fact. Only a declared type ([SP])
        # outranks it.
        guessed = fields.evidence.get("type") == "wording that admits only one answer"
        if ceiling and ceiling > 1 and fields.qtype is QuestionType.SINGLE and guessed:
            fields.qtype = None
        if fields.qtype is None and ceiling:
            fields.qtype = (
                QuestionType.MULTI if ceiling > 1 else QuestionType.SINGLE
            )
            fields.evidence["type"] = f"select up to {ceiling}"
            if "type" in fields.missing:
                fields.missing.remove("type")

    if range_terminate is not None:
        fields.terminate_if = range_terminate
        fields.terminate_verbatim = range_terminate.verbatim_text
        fields.evidence["terminate"] = range_terminate_evidence
    elif terminate_codes:
        fields.terminate_if = Expression.from_dict(
            {
                "op": "in",
                "qid": qid,
                # `values`, NOT `value`. Expression defines `in` as membership
                # in `values`; evaluate() reads `expression.values or []` and
                # the SurveyJS compiler emits `anyof [...]` from it. Putting the
                # codes in `value` left both reading an empty list, so every
                # screen-out this parser produced fired on NOBODY — while the
                # review UI showed an empty codes box beside a verbatim that
                # plainly said "TERMINATE if 1,3,98 selected".
                "values": sorted(terminate_codes),
                "verbatim_text": "; ".join(terminate_lines),
            }
        )
        fields.terminate_verbatim = "; ".join(terminate_lines)
        fields.evidence["terminate"] = "; ".join(terminate_lines)
    elif terminate_lines:
        # Stated in prose but not tied to option codes — preserve it and let a
        # model resolve it, never drop a screen-out.
        fields.terminate_verbatim = "; ".join(terminate_lines)
        fields.missing.append("terminate")

    if fields.qtype in (QuestionType.NUMERIC, QuestionType.OPEN_TEXT) and options:
        # The scripter declared a number box or a text box, so there is no
        # answer list — only the special codes written out beside it ("888.
        # Still enrolled", "26. Over 25 percent"). Anything without a code of
        # its own is instruction text or a stray line, and passing it off as an
        # option makes an open column look like a coded one to every check
        # downstream.
        kept = [
            option
            for option, written in zip(options, written_codes, strict=False)
            if written is not None
        ]
        if len(kept) != len(options):
            fields.options = kept
            if fields.terminate_if is not None and fields.terminate_if.values:
                # A screen-out can only test a code that survived.
                surviving = sorted(
                    v for v in fields.terminate_if.values if v in {o.code for o in kept}
                )
                if surviving:
                    fields.terminate_if.values = surviving
                else:
                    fields.terminate_if = None
                    fields.missing.append("terminate")

    # An entry with a stated number range is a number. "Fill in [0-100]" and
    # "[0-1,000] ... [OE]" both mean "type a number from here to here": OE is
    # "open entry" in these documents, not "free text", when a range is given.
    entry = (
        any(_ENTRY_PROMPT.match(d) for d in directives)
        or fields.qtype is QuestionType.OPEN_TEXT
    )
    span = next((m for d in directives if (m := _NUMBER_RANGE_STATED.fullmatch(d.strip()))), None)
    if entry and span and not fields.options and fields.qtype in (None, QuestionType.OPEN_TEXT):
        low, high = (int(g.replace(",", "")) for g in span.groups())
        if low < high:
            fields.qtype = QuestionType.NUMERIC
            fields.bounds = {"min": low, "max": high}
            fields.evidence["type"] = f"a number from {low} to {high}"

    if not fields.text:
        fields.missing.append("text")
    if fields.qtype is None:
        fields.missing.append("type")

    return fields
