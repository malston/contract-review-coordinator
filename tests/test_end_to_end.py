"""End-to-end through the real loop and tools -- the headline linchpin.

Same system, two model trajectories:
  - Happy path: extract -> risk-check -> send. The email reaches legal.
  - Distractor: the model skips the risk-check, narrates "I reviewed it," and
    calls send_email anyway. The gate blocks it; nothing leaves the outbox.

Only the protocol and real state decide the outcome -- never the narration.
"""

from contract_review.harness import build_harness
from contract_review.loop import (
    Response,
    ScriptedClient,
    run_agentic_loop,
    text_block,
    tool_use_block,
)
from contract_review.subagents import StubRunner


def _run(script, state, runner, raw):
    tools, hooks, allowed_tools, outbox = build_harness(state, runner, raw)
    messages = run_agentic_loop(
        ScriptedClient(script), [{"role": "user", "content": "Review and email legal."}],
        tools=tools, hooks=hooks, allowed_tools=allowed_tools, state=state,
    )
    return messages, outbox


def _tool_results(messages):
    return [b for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


def test_happy_path_emails_an_attributable_summary_to_legal(
    state, sample_raw, extractor_result, risk_result
):
    runner = StubRunner(extractor_result=extractor_result, risk_result=risk_result)
    script = [
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
    _, outbox = _run(script, state, runner, sample_raw)
    assert len(outbox) == 1
    assert outbox[0].to == "legal@acme.com"
    assert outbox[0].cited_clause_ids == ["12.1"]


def test_model_skips_review_claims_done_and_send_is_blocked(
    state, sample_raw, extractor_result, risk_result
):
    runner = StubRunner(extractor_result=extractor_result, risk_result=risk_result)
    script = [
        Response("tool_use", [tool_use_block("e1", "pdf_extract", {})]),
        Response("tool_use", [tool_use_block("x1", "Task", {"subagent_type": "extractor"})]),
        # No risk_checker. Prose claims the review happened; the model calls send.
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
    messages, outbox = _run(script, state, runner, sample_raw)
    assert outbox == []  # nothing reached legal
    blocked = [r for r in _tool_results(messages) if r["is_error"]]
    assert len(blocked) == 1
    assert "not complete" in blocked[0]["content"]


def test_completed_review_blocks_email_citing_an_unverified_clause(
    state, sample_raw, extractor_result, risk_result
):
    # The review really completes (extractor + risk-check run), doc matches, and
    # the recipient is legal -- but the email cites a clause the reviewer never
    # verified. The gate's citation check must still block it.
    runner = StubRunner(extractor_result=extractor_result, risk_result=risk_result)
    script = [
        Response("tool_use", [tool_use_block("e1", "pdf_extract", {})]),
        Response("tool_use", [tool_use_block("x1", "Task", {"subagent_type": "extractor"})]),
        Response("tool_use", [tool_use_block("r1", "Task", {"subagent_type": "risk_checker"})]),
        Response("tool_use", [tool_use_block("s1", "send_email", {
            "to": "legal@acme.com",
            "subject": "Contract review: acme_msa.pdf",
            "body": "Clause 99.9 is a problem.",
            "cited_clause_ids": ["99.9"],  # never verified
        })]),
        Response("end_turn", [text_block("done")]),
    ]
    messages, outbox = _run(script, state, runner, sample_raw)
    assert outbox == []
    blocked = [r for r in _tool_results(messages) if r["is_error"]]
    assert any("verified" in r["content"] for r in blocked)
