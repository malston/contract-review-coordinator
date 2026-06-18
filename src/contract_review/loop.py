"""The coordinator's agentic loop -- Messages-API shaped, model-agnostic.

Every control-flow decision keys off the protocol: `stop_reason`, the block
`type`, and `tool_use_id`. None of them read the assistant's prose. Termination
is `stop_reason == "end_turn"`; any other stop reason is unexpected and raises
(a non-end_turn is never silently treated as "done"); the step cap is a safety
backstop that raises, not a completion signal.

Tools are gated and transformed by hooks shaped like the Claude Agent SDK's:
`hooks` maps an event (`"PreToolUse"`, `"PostToolUse"`) to a list of
`HookMatcher`s, each hook is `(input_data, tool_use_id, context) -> dict`, a
`PreToolUse` hook denies by returning `permissionDecision: "deny"`, and a
`PostToolUse` hook rewrites a tool's output via `updatedToolOutput`. `allowed_tools`
is the allowlist: a tool not on it is refused before it runs.

`ModelClient` is the seam. `ScriptedClient` replays canned responses so the loop
can be exercised offline; `ClaudeClient` (in `live.py`) calls the real API.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_use_block(block_id: str, name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def tool_result_block(tool_use_id: str, content: Any, *, is_error: bool = False) -> dict:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": text,
        "is_error": is_error,
    }


@dataclass
class Response:
    stop_reason: str
    content: list[dict]


# A hook is (input_data, tool_use_id, context) -> dict, matching the SDK signature.
Hook = Callable[[dict, str, Any], dict]


@dataclass
class HookMatcher:
    """Mirror of `claude_agent_sdk.HookMatcher`: `matcher` is the tool name to
    match (`None` matches every tool); `hooks` run in order."""

    matcher: str | None
    hooks: list[Hook]


class LoopError(RuntimeError):
    pass


class ModelClient(Protocol):
    def create(self, messages: list[dict]) -> Response: ...


class ScriptedClient:
    """Replays a fixed list of Responses -- offline driver for the loop."""

    def __init__(self, responses: list[Response]):
        self._responses = list(responses)
        self._index = 0

    def create(self, messages: list[dict]) -> Response:
        if self._index >= len(self._responses):
            raise LoopError("ScriptedClient exhausted: loop asked for another turn.")
        response = self._responses[self._index]
        self._index += 1
        return response


def _matching_hooks(
    hooks: dict[str, list[HookMatcher]], event: str, tool_name: str
) -> Iterator[Hook]:
    for matcher in hooks.get(event, []):
        if matcher.matcher is None or matcher.matcher == tool_name:
            yield from matcher.hooks


# An irreversible action must never run on a signal the gate doesn't understand.
# Only an explicit allow (an empty dict, or permissionDecision "allow") proceeds;
# every other recognized blocking signal blocks, and anything else fails closed.
_ALLOW_DECISIONS = {None, "allow"}


def _pre_tool_deny_reason(
    hooks: dict[str, list[HookMatcher]], tool_name: str, tool_input: dict,
    tool_use_id: str, context: Any,
) -> str | None:
    """Run PreToolUse hooks; return a deny reason if any blocks, else None.

    Fails closed: an `"ask"`/`"defer"`/unrecognized `permissionDecision`, or a
    non-dict return, raises rather than silently allowing the tool to run.
    """
    input_data = {"tool_name": tool_name, "tool_input": tool_input}
    for hook in _matching_hooks(hooks, "PreToolUse", tool_name):
        out = hook(input_data, tool_use_id, context)
        if not isinstance(out, dict):
            raise LoopError(
                f"PreToolUse hook for {tool_name!r} returned a non-dict "
                f"({type(out).__name__}); refusing to default to allow."
            )
        # Top-level stop signals (SDK): decision "block" / continue_ False.
        if out.get("decision") == "block" or out.get("continue_") is False:
            return out.get("reason") or out.get("stopReason") or "blocked by PreToolUse hook"
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision == "deny":
            return out["hookSpecificOutput"].get(
                "permissionDecisionReason", "blocked by PreToolUse hook"
            )
        if decision not in _ALLOW_DECISIONS:
            raise LoopError(
                f"PreToolUse hook for {tool_name!r} returned unhandled "
                f"permissionDecision {decision!r}; refusing to default to allow."
            )
    return None


def _apply_post_tool_hooks(
    hooks: dict[str, list[HookMatcher]], tool_name: str, tool_input: dict,
    result: Any, tool_use_id: str, context: Any,
) -> Any:
    """Run PostToolUse hooks; each may replace the result via updatedToolOutput."""
    input_data = {"tool_name": tool_name, "tool_input": tool_input, "tool_response": result}
    for hook in _matching_hooks(hooks, "PostToolUse", tool_name):
        spec = (hook(input_data, tool_use_id, context) or {}).get("hookSpecificOutput", {})
        if "updatedToolOutput" in spec:
            result = spec["updatedToolOutput"]
            input_data["tool_response"] = result
    return result


def run_agentic_loop(
    client: ModelClient,
    messages: list[dict],
    *,
    tools: dict[str, Callable[[dict, Any], Any]],
    hooks: dict[str, list[HookMatcher]] | None = None,
    allowed_tools: list[str] | None = None,
    state: Any = None,
    max_steps: int = 25,
) -> list[dict]:
    hooks = hooks or {}
    for _ in range(max_steps):
        response = client.create(messages)
        # Append the assistant turn verbatim -- thinking and tool_use blocks intact.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return messages  # the ONLY completion signal
        if response.stop_reason != "tool_use":
            # max_tokens / pause_turn / refusal would be handled here in production;
            # none of them mean "done". An unhandled one must raise, not complete.
            raise LoopError(f"unexpected stop_reason: {response.stop_reason}")

        tool_results = []
        for block in response.content:
            if block.get("type") != "tool_use":
                continue  # prose and thinking never drive control flow
            name, tool_use_id, tool_input = block["name"], block["id"], block["input"]

            if allowed_tools is not None and name not in allowed_tools:
                tool_results.append(tool_result_block(
                    tool_use_id, f"tool {name!r} is not in allowed_tools", is_error=True
                ))
                continue

            deny_reason = _pre_tool_deny_reason(hooks, name, tool_input, tool_use_id, state)
            if deny_reason is not None:
                tool_results.append(tool_result_block(tool_use_id, deny_reason, is_error=True))
                continue

            if name not in tools:
                raise LoopError(f"allow-listed tool {name!r} has no registered implementation")
            result = tools[name](tool_input, state)
            result = _apply_post_tool_hooks(hooks, name, tool_input, result, tool_use_id, state)
            tool_results.append(tool_result_block(tool_use_id, result))

        # Tool results go back as a single role:"user" turn, ids matched 1:1.
        messages.append({"role": "user", "content": tool_results})

    raise LoopError("max steps exceeded -- backstop reached, not a completion.")
