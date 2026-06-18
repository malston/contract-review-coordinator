"""The agentic loop: control flow reads the protocol, not the prose.

Termination is `stop_reason == "end_turn"` and nothing else. Text that claims
"I'm done" never ends the loop. An iteration cap is a safety backstop that
raises -- never a completion signal.
"""

import json

import pytest

from contract_review.loop import (
    LoopError,
    Response,
    ScriptedClient,
    run_agentic_loop,
    text_block,
    tool_use_block,
)


def _echo_recording(calls):
    def echo(tool_input, state):
        calls.append(tool_input)
        return {"ok": True}

    return echo


def test_terminates_on_end_turn():
    client = ScriptedClient([Response("end_turn", [text_block("done")])])
    messages = run_agentic_loop(
        client, [{"role": "user", "content": "go"}],
        tools={}, pre_hooks={}, post_hooks={}, state=None,
    )
    assert messages[-1]["role"] == "assistant"


def test_dispatches_tool_then_threads_result_as_user_turn():
    calls = []
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "echo", {"x": 1})]),
        Response("end_turn", [text_block("finished")]),
    ])
    messages = run_agentic_loop(
        client, [], tools={"echo": _echo_recording(calls)},
        pre_hooks={}, post_hooks={}, state=None,
    )
    assert calls == [{"x": 1}]
    results = [b for m in messages if m["role"] == "user"
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert results[0]["tool_use_id"] == "t1"


def test_text_claiming_done_does_not_terminate_only_stop_reason_does():
    calls = []
    client = ScriptedClient([
        # stop_reason is tool_use even though the prose says "done" -> keep going
        Response("tool_use", [text_block("All done, terminating now."),
                              tool_use_block("t1", "echo", {})]),
        Response("end_turn", [text_block("real end")]),
    ])
    run_agentic_loop(
        client, [], tools={"echo": _echo_recording(calls)},
        pre_hooks={}, post_hooks={}, state=None,
    )
    assert calls == [{}]  # it ran the tool despite the "done" narration


def test_unexpected_stop_reason_raises_never_treated_as_done():
    client = ScriptedClient([Response("max_tokens", [text_block("...")])])
    with pytest.raises(LoopError, match="stop_reason"):
        run_agentic_loop(client, [], tools={}, pre_hooks={}, post_hooks={}, state=None)


def test_iteration_cap_raises_rather_than_reporting_success():
    # Always asks for a tool; never emits end_turn -> the cap must raise.
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t", "echo", {})]) for _ in range(10)
    ])
    with pytest.raises(LoopError, match="max steps"):
        run_agentic_loop(
            client, [], tools={"echo": lambda i, s: {"ok": True}},
            pre_hooks={}, post_hooks={}, state=None, max_steps=3,
        )


def test_pre_hook_block_short_circuits_the_tool():
    from contract_review.gate import GateDecision

    ran = []
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "danger", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    messages = run_agentic_loop(
        client, [],
        tools={"danger": lambda i, s: ran.append(1)},
        pre_hooks={"danger": lambda i, s: GateDecision(False, "blocked: nope")},
        post_hooks={}, state=None,
    )
    assert ran == []  # tool never executed
    result = [b for m in messages if m["role"] == "user"
              for b in m["content"] if b.get("type") == "tool_result"][0]
    assert result["is_error"] is True
    assert "blocked" in json.loads(json.dumps(result["content"]))
