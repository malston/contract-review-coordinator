"""The agentic loop: control flow reads the protocol, not the prose.

Termination is `stop_reason == "end_turn"` and nothing else. Text that claims
"I'm done" never ends the loop. An iteration cap is a safety backstop that
raises -- never a completion signal.

Tool calls are gated and transformed by SDK-shaped hooks: `PreToolUse` hooks may
deny a call (returning `permissionDecision: "deny"`), `PostToolUse` hooks may
rewrite a tool's output (`updatedToolOutput`), and a tool absent from
`allowed_tools` is refused before it runs.
"""

import pytest

from contract_review.loop import (
    HookMatcher,
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


def _tool_results(messages):
    return [b for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


def test_terminates_on_end_turn():
    client = ScriptedClient([Response("end_turn", [text_block("done")])])
    messages = run_agentic_loop(
        client, [{"role": "user", "content": "go"}], tools={}, state=None,
    )
    assert messages[-1]["role"] == "assistant"


def test_dispatches_tool_then_threads_result_as_user_turn():
    calls = []
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "echo", {"x": 1})]),
        Response("end_turn", [text_block("finished")]),
    ])
    messages = run_agentic_loop(
        client, [], tools={"echo": _echo_recording(calls)}, state=None,
    )
    assert calls == [{"x": 1}]
    assert _tool_results(messages)[0]["tool_use_id"] == "t1"


def test_text_claiming_done_does_not_terminate_only_stop_reason_does():
    calls = []
    client = ScriptedClient([
        # stop_reason is tool_use even though the prose says "done" -> keep going
        Response("tool_use", [text_block("All done, terminating now."),
                              tool_use_block("t1", "echo", {})]),
        Response("end_turn", [text_block("real end")]),
    ])
    run_agentic_loop(client, [], tools={"echo": _echo_recording(calls)}, state=None)
    assert calls == [{}]  # it ran the tool despite the "done" narration


def test_unexpected_stop_reason_raises_never_treated_as_done():
    client = ScriptedClient([Response("max_tokens", [text_block("...")])])
    with pytest.raises(LoopError, match="stop_reason"):
        run_agentic_loop(client, [], tools={}, state=None)


def test_iteration_cap_raises_rather_than_reporting_success():
    # Always asks for a tool; never emits end_turn -> the cap must raise.
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t", "echo", {})]) for _ in range(10)
    ])
    with pytest.raises(LoopError, match="max steps"):
        run_agentic_loop(
            client, [], tools={"echo": lambda i, s: {"ok": True}},
            state=None, max_steps=3,
        )


def test_pre_tool_use_deny_short_circuits_the_tool():
    ran = []

    def deny(input_data, tool_use_id, context):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "blocked: nope",
        }}

    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "danger", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    messages = run_agentic_loop(
        client, [],
        tools={"danger": lambda i, s: ran.append(1)},
        hooks={"PreToolUse": [HookMatcher(matcher="danger", hooks=[deny])]},
        state=None,
    )
    assert ran == []  # tool never executed
    result = _tool_results(messages)[0]
    assert result["is_error"] is True
    assert "blocked: nope" in result["content"]


def test_pre_tool_use_hook_returning_empty_allows_the_tool():
    ran = []
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "safe", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    run_agentic_loop(
        client, [],
        tools={"safe": lambda i, s: ran.append(1)},
        hooks={"PreToolUse": [HookMatcher(matcher="safe", hooks=[lambda *a: {}])]},
        state=None,
    )
    assert ran == [1]


def test_post_tool_use_hook_rewrites_the_tool_output():
    def normalize(input_data, tool_use_id, context):
        raw = input_data["tool_response"]
        return {"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": {"canonical": raw["messy"].strip().lower()},
        }}

    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "extract", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    messages = run_agentic_loop(
        client, [],
        tools={"extract": lambda i, s: {"messy": "  HELLO  "}},
        hooks={"PostToolUse": [HookMatcher(matcher="extract", hooks=[normalize])]},
        state=None,
    )
    # The model only ever sees the canonical, transformed output.
    assert '"canonical": "hello"' in _tool_results(messages)[0]["content"]


def test_tool_absent_from_allowed_tools_is_refused_before_running():
    ran = []
    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "danger", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    messages = run_agentic_loop(
        client, [],
        tools={"danger": lambda i, s: ran.append(1)},
        allowed_tools=["echo"],  # danger is not allowed
        state=None,
    )
    assert ran == []
    result = _tool_results(messages)[0]
    assert result["is_error"] is True
    assert "allowed_tools" in result["content"]


def test_hook_matcher_with_none_matches_every_tool():
    seen = []

    def record(input_data, tool_use_id, context):
        seen.append(input_data["tool_name"])
        return {}

    client = ScriptedClient([
        Response("tool_use", [tool_use_block("t1", "alpha", {})]),
        Response("tool_use", [tool_use_block("t2", "beta", {})]),
        Response("end_turn", [text_block("ok")]),
    ])
    run_agentic_loop(
        client, [],
        tools={"alpha": lambda i, s: {}, "beta": lambda i, s: {}},
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[record])]},
        state=None,
    )
    assert seen == ["alpha", "beta"]


def _one_tool_call(name="danger"):
    return [
        Response("tool_use", [tool_use_block("t1", name, {})]),
        Response("end_turn", [text_block("ok")]),
    ]


def _pre(hook, *, tool="danger", impl=None):
    return dict(
        tools={tool: impl or (lambda i, s: {"ran": True})},
        hooks={"PreToolUse": [HookMatcher(matcher=tool, hooks=[hook])]},
        state=None,
    )


def test_ask_decision_fails_closed_rather_than_running_the_tool():
    # The SDK would pause for approval on "ask"; an irreversible action must not
    # silently run. The gate substrate fails closed on anything but allow/deny.
    def ask(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask"}}

    with pytest.raises(LoopError, match="unhandled permissionDecision"):
        run_agentic_loop(ScriptedClient(_one_tool_call()), [], **_pre(ask))


def test_defer_decision_fails_closed():
    def defer(i, t, c):
        return {"hookSpecificOutput": {"permissionDecision": "defer"}}

    with pytest.raises(LoopError, match="unhandled permissionDecision"):
        run_agentic_loop(ScriptedClient(_one_tool_call()), [], **_pre(defer))


def test_non_dict_hook_return_fails_closed():
    with pytest.raises(LoopError, match="non-dict"):
        run_agentic_loop(ScriptedClient(_one_tool_call()), [], **_pre(lambda i, t, c: None))


def test_top_level_block_decision_denies_the_tool():
    ran = []

    def block(i, t, c):
        return {"decision": "block", "reason": "policy stop"}

    messages = run_agentic_loop(
        ScriptedClient(_one_tool_call()), [],
        **_pre(block, impl=lambda i, s: ran.append(1)),
    )
    assert ran == []
    assert "policy stop" in _tool_results(messages)[0]["content"]


def test_tool_on_allowlist_can_still_be_denied_by_a_hook():
    # allowlist passes, but the PreToolUse hook denies -> blocked, never run.
    ran = []

    def deny(i, t, c):
        return {"hookSpecificOutput": {
            "permissionDecision": "deny", "permissionDecisionReason": "no",
        }}

    messages = run_agentic_loop(
        ScriptedClient(_one_tool_call()), [],
        tools={"danger": lambda i, s: ran.append(1)},
        hooks={"PreToolUse": [HookMatcher(matcher="danger", hooks=[deny])]},
        allowed_tools=["danger"],
        state=None,
    )
    assert ran == []
    assert _tool_results(messages)[0]["is_error"] is True


def test_allow_listed_tool_without_an_implementation_raises():
    with pytest.raises(LoopError, match="no registered implementation"):
        run_agentic_loop(
            ScriptedClient(_one_tool_call("ghost")), [],
            tools={}, allowed_tools=["ghost"], state=None,
        )


def test_chained_post_tool_use_hooks_thread_the_output():
    def wrap_a(i, t, c):
        return {"hookSpecificOutput": {"updatedToolOutput": {"v": i["tool_response"]["v"] + "-a"}}}

    def wrap_b(i, t, c):
        return {"hookSpecificOutput": {"updatedToolOutput": {"v": i["tool_response"]["v"] + "-b"}}}

    messages = run_agentic_loop(
        ScriptedClient([
            Response("tool_use", [tool_use_block("t1", "extract", {})]),
            Response("end_turn", [text_block("ok")]),
        ]), [],
        tools={"extract": lambda i, s: {"v": "x"}},
        hooks={"PostToolUse": [HookMatcher(matcher="extract", hooks=[wrap_a, wrap_b])]},
        state=None,
    )
    # second hook transforms the first hook's output: x -> x-a -> x-a-b
    assert '"v": "x-a-b"' in _tool_results(messages)[0]["content"]
