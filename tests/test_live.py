"""The only logic in the live path worth testing offline: pulling the subagent's
JSON out of model text. The network wrappers (ClaudeClient/ClaudeRunner) are a
thin, key-gated boundary and are exercised against the real API, not mocks.
"""

import asyncio

import pytest

from contract_review.live import parse_subagent_output
from contract_review.schemas import EmailRequest
from contract_review.state import CoordinatorState


def test_parses_clean_json():
    out = parse_subagent_output('{"liability_clauses": ["12.1", "8.4"]}')
    assert out == {"liability_clauses": ["12.1", "8.4"]}


def test_parses_json_inside_code_fences():
    out = parse_subagent_output('```json\n{"verdicts": []}\n```')
    assert out == {"verdicts": []}


def test_parses_json_with_surrounding_prose():
    out = parse_subagent_output('Here is the result:\n{"a": [1, 2]}\nLet me know.')
    assert out == {"a": [1, 2]}


def test_raises_when_no_json_object_present():
    with pytest.raises(ValueError, match="JSON"):
        parse_subagent_output("I could not complete the task.")


def _state() -> CoordinatorState:
    return CoordinatorState(
        contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf", doc_sha256="sha",
    )


def test_build_agent_options_wires_the_real_sdk_surface():
    pytest.importorskip("claude_agent_sdk")
    from contract_review.live import build_agent_options

    options = build_agent_options(
        _state(), resume="sess-1", fork_session=True, session_id="sess-2"
    )
    assert set(options.agents) == {"extractor", "risk_checker"}
    assert "Task" in options.allowed_tools  # required to spawn subagents
    assert {m.matcher for m in options.hooks["PreToolUse"]} == {"send_email"}
    assert {m.matcher for m in options.hooks["PostToolUse"]} == {"pdf_extract"}
    assert (options.resume, options.fork_session, options.session_id) == (
        "sess-1", True, "sess-2",
    )


def test_real_sdk_pre_tool_hook_denies_when_review_incomplete():
    pytest.importorskip("claude_agent_sdk")
    from contract_review.live import build_agent_options

    options = build_agent_options(_state())  # no review in state
    gate = options.hooks["PreToolUse"][0].hooks[0]
    email = EmailRequest(
        to="legal@acme.com", subject="x", body="y", cited_clause_ids=[],
    )
    out = asyncio.run(gate(
        {"tool_name": "send_email", "tool_input": email.model_dump()}, "t1", None,
    ))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

