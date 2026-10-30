"""Which tools the model sees for one question.

All 47 tools went with every call: about 6,200 tokens of descriptions, and a
choice among all of them. Most wrong answers in the evals were wrong choices
(age for "gender", the Cleaning screen for a how-to), not missing abilities.
So each question is offered the core tools plus the areas its screen and its
words point to. Nothing matched means everything is offered, as before, and
every retry the Copilot makes widens to everything — routing can only narrow
a first attempt, never block an answer.
"""

from __future__ import annotations

import re

#: Always offered: finding your way around, whatever the question.
CORE = ("list_studies", "study_overview", "project_lifecycle", "search_project",
        "product_help", "data_variables", "find_question", "propose_remember")

AREAS: dict[str, tuple[str, ...]] = {
    "questionnaire": (
        "question_details", "who_was_asked", "question_graph", "reading_conventions",
        "extraction_diagnosis", "study_brief", "variable_plan", "dry_run",
        "propose_reextract", "propose_edit_question", "propose_add_hidden_variable",
        "propose_reread_question",
        "propose_question_in_link", "propose_approve_question", "propose_delete_question",
        "propose_teach_reading", "propose_approve_clean"),
    "fielding": (
        "field_report", "soft_launch_check", "field_projection", "dry_run",
        "fieldwork_from_data", "vendor_scorecard", "propose_open_link", "propose_set_hold",
        "propose_close_link", "propose_set_look", "propose_add_quota", "propose_materialize"),
    "data": ("make_tables", "excel_report", "ask_data", "analysis_methods", "run_method",
             "variable_plan", "weighting_status", "propose_weighting",
             "data_vs_questionnaire", "report_findings"),
    "cleaning": ("cleaning_status", "qc_findings", "fieldwork_from_data",
                 "propose_remove_respondents", "propose_lock_cleaning"),
    "open_ends": ("open_end_coding", "search_verbatims", "propose_code_open_ends"),
    "reports": ("report_findings", "make_tables", "excel_report", "ask_data",
                "propose_draft_report"),
    "trust": ("compliance_check", "ledger_check", "qc_findings", "data_vs_questionnaire"),
    "admin": ("team_and_costs",),
}

# The words that point at an area. Broad on purpose: an extra area costs a few
# hundred tokens; a missing one costs a wrong first attempt.
_INTENT: dict[str, re.Pattern] = {area: re.compile(words, re.I) for area, words in {
    "questionnaire": r"question(?!s? (?:about|on) how)|qnre|routing|skip|option|wording|"
                     r"screener|base\b|who (?:was|is) asked|hidden|derived|piping|mask|"
                     r"approve|re-?read|teach|parser|logic|dry run|reachable|depends",
    "fielding": r"link|quota|soft.?launch|hold|release|close|complete|incidence|field|"
                r"sample|vendor|supplier|projection|finish|terminat|disposition|look|theme|"
                r"template|data file|materiali",
    "data": r"table|crosstab|cross.?tab|banner|by (?:age|gender|region|generation|\w+)|"
            r"chart|graph|plot|percent|%|average|mean|significan|turf|maxdiff|"
            r"van westendorp|driver|price|variable|column|weight|net\b|top.?2|breakdown|"
            r"excel|workbook|spreadsheet|xlsx|refresh|weight|rake|raking|rim\b|target",
    "cleaning": r"clean|speeder|straight.?lin|flag|duplicate|bot\b|fraud|authentic|"
                r"remove|bad respondent|quality|lock",
    "open_ends": r"open.?end|verbatim|coded|coding|codeframe|said|comment|write.?in|\bcode\b",
    "reports": r"finding|report|insight|summary|topline|deck|powerpoint|story|draft",
    "trust": r"compliance|adverse|privacy|notice|ledger|certificate|tamper|verif|"
             r"match(?:es)? (?:the|its) questionnaire|discrepanc",
    "admin": r"cost|spend|bill|usage|team|user|who (?:is|are) on|invite|aws",
}.items()}

# The screen the question is asked from.
_PAGE: tuple[tuple[str, str], ...] = (
    ("questionnaire", "questionnaire"), ("fielding", "fielding"), ("cleaning", "cleaning"),
    ("open", "open_ends"), ("coding", "open_ends"), ("report", "reports"),
    ("insight", "reports"), ("table", "data"), ("crosstab", "data"), ("data", "data"),
    ("variable", "data"), ("admin", "admin"), ("compliance", "trust"),
)

#: What the panel shows while a tool runs.
LABELS = {
    "list_studies": "Looking through the projects", "study_overview": "Reading the project",
    "project_lifecycle": "Checking where the project stands",
    "search_project": "Searching the project", "product_help": "Reading the help",
    "data_variables": "Reading the variable list", "find_question": "Finding the question",
    "question_details": "Reading the question", "who_was_asked": "Working out who was asked",
    "question_graph": "Following the routing", "variable_plan": "Reading the hidden-variable plan",
    "dry_run": "Simulating respondents", "field_report": "Reading the field report",
    "soft_launch_check": "Running the soft-launch check",
    "field_projection": "Projecting the fieldwork", "fieldwork_from_data": "Reading the fieldwork",
    "vendor_scorecard": "Scoring the vendors", "make_tables": "Computing the tables",
    "excel_report": "Building the Excel report", "weighting_status": "Reading the weighting",
    "ask_data": "Asking the data", "analysis_methods": "Checking which methods fit",
    "run_method": "Running the analysis",
    "data_vs_questionnaire": "Comparing data and questionnaire",
    "report_findings": "Reading the findings", "cleaning_status": "Reading the Cleaning screen",
    "qc_findings": "Reading the quality checks", "open_end_coding": "Reading the open-end coding",
    "search_verbatims": "Searching what respondents wrote",
    "compliance_check": "Checking compliance", "ledger_check": "Verifying the record",
    "team_and_costs": "Reading the team and costs",
}


def label_for(tool: str) -> str:
    if tool in LABELS:
        return LABELS[tool]
    return "Preparing a change for you to confirm" if tool.startswith("propose_") else \
        f"Looking up {tool.replace('_', ' ')}"


def areas_for(message: str, screen: str = "", earlier_tools: list[str] | None = None) -> set[str]:
    found = {area for area, words in _INTENT.items() if words.search(message)}
    screen = screen.lower()
    found |= {area for key, area in _PAGE if key in screen}
    # "1", "yes", "try again": a follow-up carries no words of its own.
    if len(message.split()) <= 3 or not found:
        found |= {a for a, tools in AREAS.items() for t in earlier_tools or [] if t in tools}
    return found


# Questions that need reasoning or writing, not a lookup: the larger model first.
_HEAVY = re.compile(
    r"\b(why|explain|interpret|summar|insight|story|storyline|recommend|implication|"
    r"compare|plan|strategy|draft|write|narrative|what (?:does|do) (?:this|these|it) mean|"
    r"so what|analy[sz]|deep.?dive|executive|takeaway|hypothes)", re.I)


#: Asking for a change to the questionnaire: several fields, conditions, a new
#: variable — planned by the larger model, not the quick one.
_CHANGE = re.compile(r"\b(apply|set|change|make|add|create|fix|update|implement|code|"
                     r"terminate|exclusive|range|condition)\b", re.I)


def is_heavy(message: str, areas: set[str]) -> bool:
    """Reasoning, explaining or writing — or a long, multi-part request, or a
    change to the questionnaire."""
    return (bool(_HEAVY.search(message)) or len(message) > 280 or "reports" in areas
            or ("questionnaire" in areas and bool(_CHANGE.search(message))))


def route(message: str, all_tools: list[str], screen: str = "",
          earlier_tools: list[str] | None = None) -> list[str]:
    """The tool names to offer, in the order of `all_tools`. Everything when
    nothing points anywhere."""
    areas = areas_for(message, screen, earlier_tools)
    if not areas:
        return list(all_tools)
    wanted = set(CORE) | {t for a in areas for t in AREAS[a]}
    return [t for t in all_tools if t in wanted]


if __name__ == "__main__":
    tools = [t for ts in AREAS.values() for t in ts] + list(CORE)
    assert "make_tables" in route("crosstab Q4 by gender", tools)
    assert "propose_set_hold" not in route("crosstab Q4 by gender", tools)
    assert "cleaning_status" in route("how many speeders?", tools)
    assert route("hello there friend", tools) == tools
    assert "make_tables" in route("1", tools, earlier_tools=["make_tables"])
    assert is_heavy("why did satisfaction drop among women?", set())
    assert not is_heavy("crosstab Q4 by gender", {"data"})
    print("ok")
