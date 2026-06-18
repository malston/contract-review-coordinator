"""Harness wiring: the Task tool validates the subagent role at the boundary."""

import pytest

from contract_review.harness import build_harness
from contract_review.subagents import StubRunner


def test_task_tool_raises_on_unknown_role(
    state, sample_raw, extractor_result, risk_result
):
    runner = StubRunner(extractor_result=extractor_result, risk_result=risk_result)
    tools, _pre, _post, _outbox = build_harness(state, runner, sample_raw)
    with pytest.raises(ValueError, match="unknown subagent role"):
        tools["Task"]({"role": "bogus"}, state)
