"""An answer the copilot invented must not reach the user looking like a reading.

The system prompt has always said every fact must come from a tool called in
the same reply. Nothing enforced it: a model that answered from its own head
had its text returned as-is, and "the Greenbrook study has an incidence of
about 62% with 340 completes" is indistinguishable from a real answer — the
most expensive kind of wrong, because it is specific and confident.
"""

from unittest.mock import patch

import pytest

from src.ai.copilot import run_copilot


def _model(*turns):
    """A fake backend replaying one message per turn."""
    queue = list(turns)

    def chat(url, key_env, model, label, messages, timeout, with_tools=True,
             tools_subset=None, tools_schema=None, max_tokens=1500):
        turn = queue.pop(0) if queue else {"content": "done"}
        return {"choices": [{"message": {"role": "assistant", **turn}}]}

    return chat


def _call(chat, message="what is the incidence?", execute=None):
    with patch("src.ai.copilot._compatible_chat", chat), \
         patch.dict("os.environ", {"BEDROCK_API_KEY": "x"}):
        return run_copilot(
            message, [], execute or (lambda name, args: {"projects": []}), "D", "owner"
        )


def _tool_call(name="list_studies"):
    return {"tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": name, "arguments": "{}"}}]}


def test_an_invented_answer_is_refused_rather_than_passed_on():
    reply, results, _ = _call(_model(
        {"content": "Incidence is about 62% with 340 completes."},
        {"content": "It is 62%, roughly 340 completes."},
    ))
    assert results == []
    assert "62%" not in reply
    assert "not going to guess" in reply
    # And it names the likely cause, because the user cannot see the backend.
    assert "call tools" in reply


def test_one_nudge_is_enough_when_the_model_will_use_a_tool():
    """Asked again with the rule made explicit, a model that can call tools
    does — so the recoverable case stays an answer rather than a refusal."""
    reply, results, _ = _call(_model(
        {"content": "Incidence is about 62%."},
        _tool_call(),
        {"content": "One project, 1 complete so far."},
    ))
    assert [r["tool"] for r in results] == ["list_studies"]
    assert reply == "One project, 1 complete so far."


def test_an_answer_that_states_no_figures_needs_no_lookup():
    """"What can you do?" is answerable without touching the data, and refusing
    it would make the copilot useless at the one thing it should always manage."""
    reply, results, _ = _call(
        _model({"content": "Hi — I can look up your projects, check a link, "
                           "or read a question."}),
        message="hello, what can you do?",
    )
    assert results == []
    assert reply.startswith("Hi")


def test_a_grounded_answer_is_never_second_guessed():
    reply, results, _ = _call(_model(
        _tool_call(),
        {"content": "1 complete, incidence 100%."},
    ), execute=lambda name, args: {"projects": [{"completes": 1, "incidence_pct": 100}]})
    assert len(results) == 1
    assert reply == "1 complete, incidence 100%."


def test_todays_date_alone_does_not_count_as_a_figure():
    """The date is injected into the prompt, so a model repeating it is not
    quoting the data — and a refusal there would be the check misfiring."""
    from datetime import date

    reply, results, _ = _call(
        _model({"content": f"Nothing has been fielded. Today is {date.today().isoformat()}."}),
        message="anything happening?",
    )
    assert results == []
    assert "Today is" in reply


def test_every_backend_failing_is_still_a_clean_error():
    def dead(*args, **kwargs):
        from src.ai.router import BackendUnavailable

        raise BackendUnavailable("no key")

    with patch("src.ai.copilot._compatible_chat", dead), \
         patch.dict("os.environ", {"BEDROCK_API_KEY": "x"}), \
         pytest.raises(Exception, match="no model could answer"):
        run_copilot("hi", [], lambda n, a: {}, "D", "owner")


def test_the_prompt_forbids_substituting_another_action():
    """Asked to delete a project, Bedrock prepared close and load cards instead."""
    from src.ai.copilot import SYSTEM_PROMPT

    assert "NEVER prepare a different action in its place" in SYSTEM_PROMPT


def test_a_figure_no_tool_returned_is_sent_back_then_marked():
    """Looked something up, then wrote a number none of it held."""
    looked_up = {"projects": [{"completes": 122, "incidence_pct": 36.4}]}
    reply, results, _ = _call(_model(
        _tool_call(),
        {"content": "122 completes at 41% incidence."},
        {"content": "122 completes at 41% incidence."},
    ), execute=lambda name, args: looked_up)
    assert "Not found in the data looked up for this answer: 41" in reply

    fixed, _, _ = _call(_model(
        _tool_call(),
        {"content": "122 completes at 41% incidence."},
        {"content": "122 completes at 36.4% incidence."},
    ), execute=lambda name, args: looked_up)
    assert fixed == "122 completes at 36.4% incidence."
