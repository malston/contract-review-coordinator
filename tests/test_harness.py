"""Harness wiring: tools, SDK-shaped hooks, and the coordinator's allowed_tools."""

import pytest

from contract_review.harness import build_harness
from contract_review.subagents import StubRunner


def _harness(state, sample_raw, extractor_result, risk_result):
    runner = StubRunner(extractor_result=extractor_result, risk_result=risk_result)
    return build_harness(state, runner, sample_raw)


def test_task_tool_raises_on_unknown_subagent_type(
    state, sample_raw, extractor_result, risk_result
):
    tools, _hooks, _allowed, _outbox = _harness(state, sample_raw, extractor_result, risk_result)
    with pytest.raises(ValueError, match="unknown subagent_type"):
        tools["Task"]({"subagent_type": "bogus"}, state)


def test_allowed_tools_includes_task_so_the_coordinator_can_spawn_subagents(
    state, sample_raw, extractor_result, risk_result
):
    _tools, _hooks, allowed, _outbox = _harness(state, sample_raw, extractor_result, risk_result)
    assert "Task" in allowed  # required for a coordinator to invoke subagents
    assert {"pdf_extract", "send_email"} <= set(allowed)


def test_hooks_wire_the_gate_on_send_email_and_normalizer_on_pdf_extract(
    state, sample_raw, extractor_result, risk_result
):
    _tools, hooks, _allowed, _outbox = _harness(state, sample_raw, extractor_result, risk_result)
    assert {m.matcher for m in hooks["PreToolUse"]} == {"send_email"}
    assert {m.matcher for m in hooks["PostToolUse"]} == {"pdf_extract"}
