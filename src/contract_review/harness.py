"""Wires the contract-review tools and hooks onto the agentic loop.

Tools:        pdf_extract, Task, send_email
PreToolUse:   send_email -> the completion-state gate
PostToolUse:  pdf_extract -> the normalizer

The outbox is the simulated irreversible action: anything appended to it has
"left the building." The whole point of the gate is that the distractor
trajectory never appends to it.
"""

from collections.abc import Callable
from typing import Any

from contract_review.coordinator import (
    ingest_extraction,
    run_extractor,
    run_risk_check,
)
from contract_review.gate import pre_tool_send_email
from contract_review.schemas import EmailRequest
from contract_review.state import CoordinatorState
from contract_review.subagents import SubagentRunner

Tool = Callable[[dict, CoordinatorState], Any]
Hook = Callable[..., Any]


def build_harness(
    state: CoordinatorState, runner: SubagentRunner, raw_extraction: dict
) -> tuple[dict[str, Tool], dict[str, Hook], dict[str, Hook], list[EmailRequest]]:
    outbox: list[EmailRequest] = []

    def pdf_extract(tool_input: dict, state: CoordinatorState) -> dict:
        return raw_extraction

    def post_pdf_extract(raw: dict, state: CoordinatorState) -> dict:
        # PostToolUse normalizer: canonicalize before the model reasons over it.
        clauses = ingest_extraction(state, raw)
        return {"normalized_clauses": [c.model_dump(mode="json") for c in clauses]}

    def task(tool_input: dict, state: CoordinatorState) -> dict:
        role = tool_input["role"]
        if role == "extractor":
            return run_extractor(state, runner)
        if role == "risk_checker":
            review = run_risk_check(state, runner)
            return {
                "verified_clause_ids": sorted(review.verified_clause_ids),
                "flagged": [v.model_dump(mode="json") for v in review.flagged],
            }
        raise ValueError(f"unknown subagent role: {role}")

    def send_email(tool_input: dict, state: CoordinatorState) -> dict:
        email = EmailRequest(**tool_input)
        outbox.append(email)  # the irreversible external action
        return {"sent": True, "to": email.to}

    def pre_send_email(tool_input: dict, state: CoordinatorState):
        return pre_tool_send_email(EmailRequest(**tool_input), state)

    tools: dict[str, Tool] = {
        "pdf_extract": pdf_extract,
        "Task": task,
        "send_email": send_email,
    }
    pre_hooks: dict[str, Hook] = {"send_email": pre_send_email}
    post_hooks: dict[str, Hook] = {"pdf_extract": post_pdf_extract}
    return tools, pre_hooks, post_hooks, outbox
