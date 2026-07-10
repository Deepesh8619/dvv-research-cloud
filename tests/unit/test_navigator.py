"""Study navigator: agent loop orchestration and tool executors."""

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from api.routes.chat import _make_executor, _run_sql
from src.ai.navigator import navigate
from src.core.models import DataMap, Variable, VarType


@pytest.fixture
def study_dir(tmp_path) -> tuple[Path, DataMap]:
    df = pl.DataFrame({
        "respondent_id": list(range(1, 41)),
        "gender": [1, 2] * 20,
        "satisfaction": ([1, 2, 3, 4, 5] * 8),
    })
    df.write_parquet(str(tmp_path / "data.parquet"))

    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", label="ID", type=VarType.NUMERIC))
    datamap.add(
        Variable(
            name="gender",
            label="Gender",
            type=VarType.SINGLE,
            values={1: "Male", 2: "Female"},
        )
    )
    datamap.add(
        Variable(
            name="satisfaction",
            label="Satisfaction",
            type=VarType.SINGLE,
            values={1: "Very poor", 2: "Poor", 3: "Neutral", 4: "Good", 5: "Very good"},
        )
    )
    return tmp_path, datamap


def test_sql_guard_blocks_writes(study_dir):
    path, _ = study_dir
    for bad in ("DROP TABLE data", "DELETE FROM data", "SELECT 1; DROP TABLE data"):
        result = _run_sql(path / "data.parquet", bad)
        assert not result["success"]


def test_sql_select_works(study_dir):
    path, _ = study_dir
    result = _run_sql(
        path / "data.parquet",
        "SELECT gender, COUNT(*) AS n FROM data GROUP BY gender ORDER BY gender",
    )
    assert result["success"]
    assert result["row_count"] == 2
    assert result["rows"][0]["n"] == 20


def test_live_table_computation(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute("show_table", {"stub": "satisfaction", "banner": ["gender"]})
    assert result["success"]
    table = result["table"]
    assert table["title"] == "Satisfaction"
    # Total + Male + Female columns
    assert [c["label"] for c in table["columns"]] == ["Total", "Male", "Female"]
    # 5 categories + T2B/B2B nets from the 5-point scale
    assert any(r["kind"] == "net" for r in table["rows"])


def test_live_table_rejects_unknown_variable(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute("show_table", {"stub": "not_a_var"})
    assert not result["success"]
    assert "Unknown variable" in result["error"]


def test_live_table_with_filter(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute(
        "show_table",
        {"stub": "satisfaction", "banner": ["gender"], "filter": {"gender": [2]}},
    )
    assert result["success"]
    # Filtered to females: total base is 20, not 40
    assert result["table"]["columns"][0]["base"] == 20


def test_navigate_agent_loop_executes_tools():
    """Mocked model: first turn requests a tool, second turn answers."""
    calls = []

    def fake_claude(messages, system, timeout):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "query_data",
                        "input": {"sql": "SELECT 1"},
                    },
                ]
            }
        return {"content": [{"type": "text", "text": "There are 40 respondents."}]}

    executed = []

    def executor(name, params):
        executed.append((name, params))
        return {"success": True, "rows": [{"n": 40}]}

    with patch("src.ai.navigator._claude_messages", side_effect=fake_claude):
        reply, tool_results = navigate("How many respondents?", "ctx", executor)

    assert reply == "There are 40 respondents."  # final turn only, not the narration
    assert executed == [("query_data", {"sql": "SELECT 1"})]
    assert tool_results[0]["tool"] == "query_data"
    # Second model call must contain the tool result
    assert calls[1][-1]["content"][0]["type"] == "tool_result"


def test_navigate_basic_executes_single_tool(study_dir):
    """Basic mode: local model picks one tool; executor runs it."""
    path, datamap = study_dir
    from api.routes.chat import _make_executor

    execute = _make_executor(path, datamap, deck={})

    class FakeResult:
        content = {
            "answer": "Here is satisfaction by gender.",
            "tool": "show_table",
            "stub": "satisfaction",
        }
        backend = "ollama"
        model = "test"

    with patch("src.ai.navigator.run_task", return_value=FakeResult()):
        from src.ai.navigator import navigate_basic

        reply, tool_results = navigate_basic("show satisfaction", "ctx", execute)

    assert "satisfaction" in reply.lower()
    assert tool_results[0]["tool"] == "show_table"
    assert tool_results[0]["success"]
    assert tool_results[0]["table"]["title"] == "Satisfaction"


def test_navigate_basic_passes_through_requested_banner(study_dir):
    """Regression: basic mode used to have no `banner` field in its schema at
    all, so "For banner gender show crosstabs for satisfaction" silently
    dropped the banner and always rendered Total-only, regardless of what was
    asked. The local model must be able to name a banner and have it reach
    the crosstab engine.
    """
    path, datamap = study_dir
    from api.routes.chat import _make_executor

    execute = _make_executor(path, datamap, deck={})

    class FakeResult:
        content = {
            "answer": "Here is satisfaction by gender.",
            "tool": "show_table",
            "stub": "satisfaction",
            "banner": ["gender"],
        }
        backend = "ollama"
        model = "test"

    with patch("src.ai.navigator.run_task", return_value=FakeResult()):
        from src.ai.navigator import navigate_basic

        reply, tool_results = navigate_basic("for banner gender show satisfaction", "ctx", execute)

    assert tool_results[0]["success"]
    columns = [c["label"] for c in tool_results[0]["table"]["columns"]]
    assert columns == ["Total", "Male", "Female"]


def test_navigate_basic_ignores_malformed_banner(study_dir):
    """A malformed banner field (not a list of strings) must not crash the
    executor — fall back to Total-only rather than passing junk through."""
    path, datamap = study_dir
    from api.routes.chat import _make_executor

    execute = _make_executor(path, datamap, deck={})

    class FakeResult:
        content = {
            "answer": "Here is satisfaction.",
            "tool": "show_table",
            "stub": "satisfaction",
            "banner": "gender",  # not a list — malformed
        }
        backend = "ollama"
        model = "test"

    with patch("src.ai.navigator.run_task", return_value=FakeResult()):
        from src.ai.navigator import navigate_basic

        reply, tool_results = navigate_basic("show satisfaction", "ctx", execute)

    assert tool_results[0]["success"]
    columns = [c["label"] for c in tool_results[0]["table"]["columns"]]
    assert columns == ["Total"]


def test_navigate_groq_agent_loop():
    """Groq path: OpenAI-shaped tool_calls drive the same tool executor."""
    from unittest.mock import patch as _patch

    calls = []

    def fake_groq(messages, timeout, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "query_data",
                                        "arguments": '{"sql": "SELECT COUNT(*) FROM data"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "40 respondents."}}]}

    executed = []

    def executor(name, params):
        executed.append((name, params))
        return {"success": True, "rows": [{"n": 40}]}

    with _patch("src.ai.navigator._groq_chat", side_effect=fake_groq):
        from src.ai.navigator import navigate_groq

        reply, tool_results = navigate_groq("How many respondents?", "ctx", executor)

    assert reply == "40 respondents."
    assert executed == [("query_data", {"sql": "SELECT COUNT(*) FROM data"})]
    assert tool_results[0]["tool"] == "query_data"
    # The tool result must be fed back as an OpenAI 'tool' role message
    assert any(m.get("role") == "tool" for m in calls[1])


def test_repair_malformed_tool_call_recovers_fused_name():
    """The real-world bug: llama-3.3 on Groq sometimes emits a structured
    tool_call whose function.name is the tool name with the JSON arguments
    fused directly onto it — 'query_data{"sql": "..."}' — instead of a bare
    name with separate arguments."""
    from src.ai.navigator import _repair_malformed_tool_call

    fixed = _repair_malformed_tool_call({"name": 'query_data{"sql": "SELECT 1"}'})
    assert fixed == {"name": "query_data", "arguments": '{"sql": "SELECT 1"}'}


def test_repair_malformed_tool_call_passes_through_clean_calls():
    from src.ai.navigator import _repair_malformed_tool_call

    fixed = _repair_malformed_tool_call(
        {"name": "show_table", "arguments": '{"stub": "S1"}'}
    )
    assert fixed == {"name": "show_table", "arguments": '{"stub": "S1"}'}


def test_repair_malformed_tool_call_rejects_unknown_tool():
    from src.ai.navigator import _repair_malformed_tool_call

    assert _repair_malformed_tool_call({"name": 'not_a_real_tool{"x": 1}'}) is None
    assert _repair_malformed_tool_call({"name": "not_a_real_tool"}) is None


def test_repair_malformed_tool_call_rejects_invalid_embedded_json():
    from src.ai.navigator import _repair_malformed_tool_call

    assert _repair_malformed_tool_call({"name": "query_data{not valid json"}) is None


def test_navigate_groq_sanitizes_malformed_name_before_echoing():
    """Regression: an unrepaired malformed tool name got echoed verbatim into
    message history, and Groq's own API re-validates the ENTIRE history
    against the current request's tools schema on every turn — so the
    malformed name didn't just fail once, it hard-failed every later turn
    with HTTP 400 'tool call validation failed', even after the bad call.
    The sanitized echo must carry the repaired (name, arguments), never the
    original fused string, and turn 2 must succeed instead of erroring.
    """
    from unittest.mock import patch as _patch

    calls = []

    def fake_groq(messages, timeout, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": 'query_data{"sql": "SELECT COUNT(*) FROM data"}',
                                        "arguments": "",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "40 respondents."}}]}

    executed = []

    def executor(name, params):
        executed.append((name, params))
        return {"success": True, "rows": [{"n": 40}]}

    with _patch("src.ai.navigator._groq_chat", side_effect=fake_groq):
        from src.ai.navigator import navigate_groq

        reply, tool_results = navigate_groq("How many respondents?", "ctx", executor)

    assert reply == "40 respondents."
    assert executed == [("query_data", {"sql": "SELECT COUNT(*) FROM data"})]

    # The echoed assistant turn (sent back to Groq on turn 2) must carry the
    # repaired name, never the original fused string — that's what would
    # trigger Groq's own "not in request.tools" validation error otherwise.
    echoed_assistant_turn = next(m for m in calls[1] if m.get("role") == "assistant")
    echoed_name = echoed_assistant_turn["tool_calls"][0]["function"]["name"]
    assert echoed_name == "query_data"


def test_navigate_groq_drops_unrecoverable_call_without_poisoning_history():
    """If a malformed call can't be repaired at all (unknown tool, or broken
    JSON), it must be dropped silently rather than echoed — better to lose
    one tool call than hard-fail the whole conversation on the next turn."""
    from unittest.mock import patch as _patch

    def fake_groq(messages, timeout, **kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "totally_unknown_tool", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }

    def executor(name, params):
        raise AssertionError("should never execute an unrecoverable call")

    with _patch("src.ai.navigator._groq_chat", side_effect=fake_groq):
        from src.ai.navigator import ModelOutputError, navigate_groq

        with pytest.raises(ModelOutputError):
            navigate_groq("some question", "ctx", executor)


def test_navigate_openai_agent_loop():
    """OpenAI fallback path: same agent loop as Groq (shared via
    _run_agent_loop), driving the same tool executor with OpenAI-shaped
    tool_calls."""
    from unittest.mock import patch as _patch

    calls = []

    def fake_openai(messages, timeout, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "query_data",
                                        "arguments": '{"sql": "SELECT COUNT(*) FROM data"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "40 respondents."}}]}

    executed = []

    def executor(name, params):
        executed.append((name, params))
        return {"success": True, "rows": [{"n": 40}]}

    with _patch("src.ai.navigator._openai_chat", side_effect=fake_openai):
        from src.ai.navigator import navigate_openai

        reply, tool_results = navigate_openai("How many respondents?", "ctx", executor)

    assert reply == "40 respondents."
    assert executed == [("query_data", {"sql": "SELECT COUNT(*) FROM data"})]
    assert tool_results[0]["tool"] == "query_data"


def test_navigate_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from src.ai.navigator import BackendUnavailable, _openai_chat

    with pytest.raises(BackendUnavailable):
        _openai_chat([{"role": "user", "content": "hi"}], timeout=5)


def test_parse_text_tool_calls_recovers_llama_markup():
    """llama-on-Groq sometimes writes tool calls as text; we must recover them."""
    from src.ai.navigator import _parse_text_tool_calls

    a = _parse_text_tool_calls('<function(show_table){"stub":"S1","banner":[]}')
    assert a and a[0]["function"]["name"] == "show_table"

    b = _parse_text_tool_calls('<function=query_data>{"sql": "SELECT 1"}</function>')
    assert b and b[0]["function"]["name"] == "query_data"

    assert _parse_text_tool_calls("just a normal answer, no call") == []
    assert _parse_text_tool_calls('<function(unknown_tool){}') == []  # not a real tool


# --- Steward questionnaire tools (get_question_details / explain_question_base /
#     search_questionnaire) --------------------------------------------------

from src.core.survey_definition import (  # noqa: E402
    Expression,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.questionnaire.navigator_tools import (  # noqa: E402
    explain_question_base,
    get_question_context,
    get_question_details,
    search_questionnaire,
)


@pytest.fixture
def survey() -> SurveyDefinition:
    """A tiny approved questionnaire: a screener, a conditional follow-up with
    verbatim routing, and a conditional whose routing has no verbatim text."""
    q1 = Question(
        qid="S1",
        text="Do you own a car?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    q2 = Question(
        qid="Q5",
        text="How satisfied are you with your car?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Satisfied"), Option(code=2, label="Not satisfied")],
        show_if=Expression(
            op="eq",
            qid="S1",
            value=1,
            verbatim_text="Ask only if S1 = Yes (owns a car)",
        ),
        extraction_confidence={"text": 0.98},
        flags=["AMBIGUOUS_ROUTING"],
    )
    q3 = Question(
        qid="Q6",
        text="Which brand of car?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Brand A")],
        # show_if with NO verbatim_text -> must fall back to structured description
        show_if=Expression(op="eq", qid="S1", value=1),
    )
    return SurveyDefinition(study_id="study_x", status="approved", questions=[q1, q2, q3])


def test_get_question_details_happy_path(survey):
    result = get_question_details(survey, "study_x", "Q5")
    assert result["success"] and result["available"]
    q = result["question"]
    assert q["text"] == "How satisfied are you with your car?"
    assert [o["label"] for o in q["options"]] == ["Satisfied", "Not satisfied"]
    assert q["flags"] == ["AMBIGUOUS_ROUTING"]
    assert q["show_if"]["verbatim_text"] == "Ask only if S1 = Yes (owns a car)"


def test_get_question_details_no_questionnaire():
    result = get_question_details(None, "study_x", "Q5")
    assert result["success"] is False
    assert result["available"] is False
    assert "No approved questionnaire" in result["message"]


def test_get_question_details_unknown_qid(survey):
    result = get_question_details(survey, "study_x", "ZZZ")
    assert result["success"] is False
    assert result["available"] is True  # questionnaire exists, qid doesn't
    assert "not in this study's approved questionnaire" in result["message"]
    assert "S1" in result["known_qids"]


def test_get_question_details_includes_datamap_links(survey):
    links = [QuestionVariableLink(qid="Q5", variable_names=["Q5", "Q5x"], match_method="fuzzy")]
    result = get_question_details(survey, "study_x", "Q5", links=links)
    assert result["datamap_variables"]["variable_names"] == ["Q5", "Q5x"]
    # A qid with no link simply omits the field.
    assert "datamap_variables" not in get_question_details(survey, "study_x", "S1", links=links)


def test_explain_question_base_uses_verbatim(survey):
    result = explain_question_base(survey, "study_x", "Q5")
    assert result["success"] and result["conditional"] is True
    assert "Ask only if S1 = Yes (owns a car)" in result["explanation"]
    assert result["verbatim_routing"] == "Ask only if S1 = Yes (owns a car)"


def test_explain_question_base_falls_back_to_structured(survey):
    """Q6's show_if has no verbatim text -> plain-English from the structured op."""
    result = explain_question_base(survey, "study_x", "Q6")
    assert result["conditional"] is True
    assert result["verbatim_routing"] is None
    assert "answered 1 to S1" in result["explanation"]


def test_explain_question_base_unconditional(survey):
    result = explain_question_base(survey, "study_x", "S1")
    assert result["conditional"] is False
    assert "all respondents" in result["explanation"]


def test_explain_question_base_no_questionnaire():
    result = explain_question_base(None, "study_x", "Q5")
    assert result["success"] is False and result["available"] is False


def test_explain_question_base_unknown_qid(survey):
    result = explain_question_base(survey, "study_x", "ZZZ")
    assert result["success"] is False and result["available"] is True


def test_search_questionnaire_finds_by_topic(survey):
    result = search_questionnaire(survey, "study_x", "brand")
    assert result["success"] and result["n_matches"] == 1
    assert result["matches"][0]["qid"] == "Q6"
    assert result["matches"][0]["matched_in"] == "question_text"


def test_search_questionnaire_matches_routing_text(survey):
    result = search_questionnaire(survey, "study_x", "owns a car")
    qids = {m["qid"] for m in result["matches"]}
    assert "Q5" in qids  # matched via verbatim routing text
    assert result["matches"][0]["matched_in"] in ("question_text", "routing_text")


def test_search_questionnaire_no_match(survey):
    result = search_questionnaire(survey, "study_x", "nonexistent topic xyz")
    assert result["success"] and result["n_matches"] == 0


def test_search_questionnaire_empty_query(survey):
    result = search_questionnaire(survey, "study_x", "   ")
    assert result["success"] and result["matches"] == []


# --- Analyst reverse-lookup tool: get_question_context ----------------------


def test_get_question_context_resolves_stub_to_question(survey):
    links = [QuestionVariableLink(qid="Q5", variable_names=["Q5r1", "Q5r2"])]
    result = get_question_context(survey, "study_x", "Q5r1", links=links)
    assert result["success"] and result["available"] and result["linked"] is True
    assert result["qid"] == "Q5"
    assert result["text"] == "How satisfied are you with your car?"
    assert result["conditional"] is True
    # Reuses explain_question_base's prose (verbatim routing).
    assert "Ask only if S1 = Yes (owns a car)" in result["base_explanation"]


def test_get_question_context_case_insensitive_match(survey):
    links = [QuestionVariableLink(qid="Q5", variable_names=["Q5R1"])]
    result = get_question_context(survey, "study_x", "q5r1", links=links)
    assert result["linked"] is True and result["qid"] == "Q5"


def test_get_question_context_no_link_is_graceful(survey):
    links = [QuestionVariableLink(qid="Q5", variable_names=["Q5r1"])]
    result = get_question_context(survey, "study_x", "paradata_start_time", links=links)
    assert result["success"] and result["available"] and result["linked"] is False
    assert "not linked to any questionnaire question" in result["message"]


def test_get_question_context_no_questionnaire():
    result = get_question_context(None, "study_x", "Q5r1", links=None)
    assert result["success"] is False and result["available"] is False


# --- Executor dispatch wiring: links flow through _make_executor -------------


def _link_study(tmp_path: Path, survey: SurveyDefinition) -> tuple[Path, DataMap]:
    """A study whose data columns link to the `survey` fixture (S1 -> Q5 routing).

    S1==1 for the first 20 respondents (who answered Q5), S1==2 for the rest
    (who left Q5 null) — so cross_validate_routing sees Q5 answered only by
    S1==1 rows, i.e. full agreement with its `show_if` (S1 == 1).
    """
    s1 = [1] * 20 + [2] * 20
    q5 = [1, 2, 1, 2] * 5 + [None] * 20
    pl.DataFrame({"respondent_id": list(range(1, 41)), "S1": s1, "Q5": q5}).write_parquet(
        str(tmp_path / "data.parquet")
    )
    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", label="ID", type=VarType.NUMERIC))
    datamap.add(Variable(name="S1", label="Own a car?", type=VarType.SINGLE,
                         values={1: "Yes", 2: "No"}))
    datamap.add(Variable(name="Q5", label="Satisfaction", type=VarType.SINGLE,
                         values={1: "Satisfied", 2: "Not satisfied"}))
    return tmp_path, datamap


def test_executor_get_question_details_populates_datamap_variables(tmp_path, survey):
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("get_question_details", {"qid": "Q5"})
    assert result["success"]
    # The linker now runs live in the executor: Q5 links to the Q5 data column.
    assert result["datamap_variables"]["variable_names"] == ["Q5"]


def test_executor_check_question_routing_happy_path(tmp_path, survey):
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("check_question_routing", {"qid": "Q5"})
    assert result["success"] and result["checked"] is True
    assert result["qid"] == "Q5"
    # Every S1==1 row answered Q5 and satisfies show_if -> full agreement.
    assert result["agreement_pct"] == 100.0
    assert result["flagged"] is False


def test_executor_check_question_routing_not_checkable(tmp_path, survey):
    """S1 has no show_if, so it's never cross-validated -> graceful 'not checked'."""
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("check_question_routing", {"qid": "S1"})
    assert result["success"] and result["checked"] is False
    assert "could not be cross-validated" in result["message"]


def test_executor_check_question_routing_no_questionnaire(tmp_path, survey):
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=None):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("check_question_routing", {"qid": "Q5"})
    assert result["success"] is False and result["available"] is False


def test_executor_get_question_context_resolves_via_live_links(tmp_path, survey):
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("get_question_context", {"stub": "Q5"})
    assert result["success"] and result["linked"] is True
    assert result["qid"] == "Q5"
    assert "Ask only if S1 = Yes (owns a car)" in result["base_explanation"]


def test_executor_get_question_context_unlinked_variable(tmp_path, survey):
    path, datamap = _link_study(tmp_path, survey)
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        result = execute("get_question_context", {"stub": "respondent_id"})
    assert result["success"] and result["linked"] is False


def test_search_questionnaire_no_questionnaire():
    result = search_questionnaire(None, "study_x", "brand")
    assert result["success"] is False and result["available"] is False


# --- Executor dispatch: the tools reachable through _make_executor's execute()
#     with the DB/disk lookup mocked -------------------------------------------


def test_executor_dispatches_check_respondent_quality(tmp_path):
    """The chat.py executor wires check_respondent_quality to the QC engine and
    flags a fielded study's speeder by respondent_id."""
    df = pl.DataFrame(
        {
            "respondent_id": list(range(1, 13)),
            "vDurationSec": [30] + [600] * 10 + [900],  # id 1 is a speeder
            "Q5r1": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2],
        }
    )
    df.write_parquet(str(tmp_path / "data.parquet"))
    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", type=VarType.NUMERIC))
    datamap.add(Variable(name="vDurationSec", type=VarType.NUMERIC))
    datamap.add(Variable(name="Q5r1", type=VarType.SINGLE, values={i: str(i) for i in range(1, 6)}))

    execute = _make_executor(tmp_path, datamap, deck={}, study_id="study_x")
    result = execute("check_respondent_quality", {})
    assert result["success"] and result["tool"] == "check_respondent_quality"
    assert result["flagged"]["speeders"] == [1]


def test_executor_check_respondent_quality_no_data(tmp_path):
    """No data.parquet -> clean failure, never a stack trace."""
    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", type=VarType.NUMERIC))
    execute = _make_executor(tmp_path, datamap, deck={}, study_id="study_x")
    result = execute("check_respondent_quality", {})
    assert result["success"] is False and "not available" in result["error"]


def test_executor_dispatches_questionnaire_tools(study_dir, survey):
    """The chat.py executor wires the questionnaire tools; _load_approved_survey
    is the single DB/disk seam we patch."""
    path, datamap = study_dir
    with patch("api.routes.chat._load_approved_survey", return_value=survey):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        details = execute("get_question_details", {"qid": "Q5"})
        assert details["success"] and details["qid"] == "Q5"
        base = execute("explain_question_base", {"qid": "Q5"})
        assert "Ask only if S1 = Yes" in base["explanation"]
        found = execute("search_questionnaire", {"query": "brand"})
        assert found["matches"][0]["qid"] == "Q6"


def test_executor_questionnaire_tools_degrade_without_questionnaire(study_dir):
    """A study with no approved questionnaire returns a clean 'not available'
    result, never an exception."""
    path, datamap = study_dir
    with patch("api.routes.chat._load_approved_survey", return_value=None):
        execute = _make_executor(path, datamap, deck={}, study_id="study_x")
        for name, params in (
            ("get_question_details", {"qid": "Q5"}),
            ("explain_question_base", {"qid": "Q5"}),
            ("search_questionnaire", {"query": "brand"}),
        ):
            result = execute(name, params)
            assert result["success"] is False
            assert result["available"] is False
