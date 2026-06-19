"""Runnable offline demo: `python -m contract_review.demo`.

Drives the real loop and tools with a ScriptedClient (no API key) along two
trajectories of the same system:

  - happy:      extract -> risk-check -> send         => the summary reaches legal
  - distractor: extract -> "I reviewed it" -> send    => the gate blocks the send

The only difference is whether the risk-check actually ran. The model's claim
that it reviewed the contract changes nothing.
"""

from dataclasses import dataclass

from contract_review.harness import build_harness
from contract_review.loop import (
    Response,
    ScriptedClient,
    run_agentic_loop,
    text_block,
    tool_use_block,
)
from contract_review.sample_contract import EXTRACTOR_RESULT, RISK_RESULT, SAMPLE_RAW
from contract_review.state import CoordinatorState
from contract_review.subagents import StubRunner

HAPPY_SCRIPT = [
    Response("tool_use", [tool_use_block("e1", "pdf_extract", {})]),
    Response("tool_use", [tool_use_block("x1", "Task", {"subagent_type": "extractor"})]),
    Response("tool_use", [tool_use_block("r1", "Task", {"subagent_type": "risk_checker"})]),
    Response("tool_use", [tool_use_block("s1", "send_email", {
        "to": "legal@acme.com",
        "subject": "Contract review: acme_msa.pdf",
        "body": "Clause 12.1 (p.9, acme_msa.pdf): exposure $5000000 exceeds the cap.",
        "cited_clause_ids": ["12.1"],
    })]),
    Response("end_turn", [text_block("Summary sent to legal.")]),
]

DISTRACTOR_SCRIPT = [
    Response("tool_use", [tool_use_block("e1", "pdf_extract", {})]),
    Response("tool_use", [tool_use_block("x1", "Task", {"subagent_type": "extractor"})]),
    # No risk_checker. The model narrates that it reviewed the contract, then sends.
    Response("tool_use", [
        text_block("I have fully reviewed the contract and it is safe to send."),
        tool_use_block("s1", "send_email", {
            "to": "legal@acme.com",
            "subject": "Reviewed",
            "body": "No issues found.",
            "cited_clause_ids": [],
        }),
    ]),
    Response("end_turn", [text_block("Done.")]),
]


@dataclass
class Outcome:
    name: str
    sent: list[str]
    blocked: list[str]


def run_trajectory(name: str, script: list[Response]) -> Outcome:
    state = CoordinatorState(
        contract_id="vendor-acme-msa-2026",
        source_name="acme_msa.pdf",
        doc_sha256="sha256-acme",
    )
    runner = StubRunner(extractor_result=EXTRACTOR_RESULT, risk_result=RISK_RESULT)
    tools, hooks, allowed_tools, outbox = build_harness(state, runner, SAMPLE_RAW)
    messages = run_agentic_loop(
        ScriptedClient(script),
        [{"role": "user", "content": "Review the contract and email legal."}],
        tools=tools, hooks=hooks, allowed_tools=allowed_tools, state=state,
    )
    blocked = [
        block["content"]
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result" and block["is_error"]
    ]
    return Outcome(name=name, sent=[email.to for email in outbox], blocked=blocked)


def main() -> None:
    for name, script in [("happy", HAPPY_SCRIPT), ("distractor", DISTRACTOR_SCRIPT)]:
        outcome = run_trajectory(name, script)
        print(f"=== {name} ===")
        if outcome.sent:
            print(f"  SENT to {', '.join(outcome.sent)}")
        else:
            print("  nothing sent")
        for reason in outcome.blocked:
            print(f"  BLOCKED: {reason}")
        print()


if __name__ == "__main__":
    main()
