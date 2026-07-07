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
