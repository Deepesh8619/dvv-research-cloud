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


def test_navigate_groq_agent_loop():
    """Groq path: OpenAI-shaped tool_calls drive the same tool executor."""
    from unittest.mock import patch as _patch

    calls = []

    def fake_groq(messages, timeout):
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
