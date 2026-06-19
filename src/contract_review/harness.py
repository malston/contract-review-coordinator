"""Wires the contract-review tools and hooks onto the agentic loop.

Tools:        pdf_extract, Task, send_email   (the coordinator's `allowed_tools`)
PreToolUse:   send_email -> the completion-state gate
PostToolUse:  pdf_extract -> the normalizer

`allowed_tools` must include `"Task"` for the coordinator to spawn subagents.
The outbox is the simulated irreversible action: anything appended to it has
"left the building." The whole point of the gate is that the distractor
trajectory never appends to it.
"""

from collections.abc import Callable
from typing import Any

from contract_review.coordinator import (
    make_pdf_extract_hook,
    run_extractor,
    run_risk_check,
)
from contract_review.gate import make_send_email_hook
from contract_review.loop import HookMatcher
from contract_review.schemas import EmailRequest
from contract_review.state import CoordinatorState
from contract_review.subagents import SubagentRunner

Tool = Callable[[dict, CoordinatorState], Any]

# The coordinator's allowlist -- "Task" is required to invoke subagents at all.
COORDINATOR_ALLOWED_TOOLS = ["pdf_extract", "Task", "send_email"]


def build_harness(
    state: CoordinatorState, runner: SubagentRunner, raw_extraction: dict
) -> tuple[dict[str, Tool], dict[str, list[HookMatcher]], list[str], list[EmailRequest]]:
    outbox: list[EmailRequest] = []

    def pdf_extract(tool_input: dict, state: CoordinatorState) -> dict:
        return raw_extraction

    def task(tool_input: dict, state: CoordinatorState) -> dict:
        subagent_type = tool_input["subagent_type"]
        if subagent_type == "extractor":
            return run_extractor(state, runner)
        if subagent_type == "risk_checker":
            review = run_risk_check(state, runner)
            return {
                "verified_clause_ids": sorted(review.verified_clause_ids),
                "flagged": [v.model_dump(mode="json") for v in review.flagged],
            }
        raise ValueError(f"unknown subagent_type: {subagent_type}")

    def send_email(tool_input: dict, state: CoordinatorState) -> dict:
        email = EmailRequest(**tool_input)
        outbox.append(email)  # the irreversible external action
        return {"sent": True, "to": email.to}

    tools: dict[str, Tool] = {
        "pdf_extract": pdf_extract,
        "Task": task,
        "send_email": send_email,
    }
    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [HookMatcher(matcher="send_email", hooks=[make_send_email_hook(state)])],
        "PostToolUse": [HookMatcher(matcher="pdf_extract", hooks=[make_pdf_extract_hook(state)])],
    }
    return tools, hooks, COORDINATOR_ALLOWED_TOOLS, outbox
