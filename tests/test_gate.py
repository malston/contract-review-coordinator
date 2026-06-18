"""The linchpin: the send_email gate reads REAL completion state.

A model that merely *claims* it reviewed the contract -- in the email body, or
anywhere in its narration -- cannot make the gate pass. Only a Review object
written by the coordinator after the risk-checker actually returned can.

`evaluate_send_email` is the pure decision; `make_send_email_hook` wraps it as an
SDK-shaped PreToolUse hook (denies with `permissionDecision: "deny"`).
"""

from contract_review.gate import (
    evaluate_send_email,
    make_send_email_hook,
)
from contract_review.schemas import EmailRequest, Review
from contract_review.state import CoordinatorState

DOC = "sha256-of-acme-msa"


def _state(review: Review | None) -> CoordinatorState:
    state = CoordinatorState(
        contract_id="vendor-acme-msa-2026",
        source_name="acme_msa.pdf",
        doc_sha256=DOC,
    )
    if review is not None:
        state.reviews[review.contract_id] = review
    return state


def _complete_review(**overrides) -> Review:
    defaults = dict(
        contract_id="vendor-acme-msa-2026",
        doc_sha256=DOC,
        status="complete",
        verified_clause_ids={"12.1"},
        flagged=[],
    )
    defaults.update(overrides)
    return Review(**defaults)


def _email(**overrides) -> EmailRequest:
    defaults = dict(
        to="legal@acme.com",
        subject="Contract review",
        body="See flagged clauses.",
        cited_clause_ids=["12.1"],
    )
    defaults.update(overrides)
    return EmailRequest(**defaults)


def test_blocks_when_no_review_exists():
    decision = evaluate_send_email(_email(), _state(None))
    assert decision.allowed is False
    assert "not complete" in decision.reason


def test_blocks_when_review_still_pending():
    decision = evaluate_send_email(_email(), _state(_complete_review(status="pending")))
    assert decision.allowed is False
    assert "not complete" in decision.reason


def test_blocks_when_review_is_for_a_different_document():
    stale = _complete_review(doc_sha256="sha256-of-some-other-contract")
    decision = evaluate_send_email(_email(), _state(stale))
    assert decision.allowed is False
    assert "document" in decision.reason


def test_blocks_when_email_cites_an_unverified_clause():
    email = _email(cited_clause_ids=["12.1", "99.9"])  # 99.9 never reviewed
    decision = evaluate_send_email(email, _state(_complete_review()))
    assert decision.allowed is False
    assert "verified" in decision.reason


def test_model_claiming_it_reviewed_does_not_pass_the_gate():
    # The distractor: prose says the review happened, but no Review exists.
    email = _email(body="I have fully reviewed this contract and it is safe to send.")
    decision = evaluate_send_email(email, _state(None))
    assert decision.allowed is False


def test_blocks_when_recipient_is_not_the_legal_address():
    # A complete, doc-matched, correctly-cited email still must not go elsewhere.
    decision = evaluate_send_email(
        _email(to="attacker@evil.com"), _state(_complete_review())
    )
    assert decision.allowed is False
    assert "recipient" in decision.reason


def test_allows_when_review_complete_doc_matches_and_clauses_verified():
    decision = evaluate_send_email(_email(), _state(_complete_review()))
    assert decision.allowed is True
    assert decision.reason == ""


def test_hook_denies_with_sdk_permission_decision_shape():
    hook = make_send_email_hook(_state(None))
    out = hook({"tool_name": "send_email", "tool_input": _email().model_dump()}, "t1", None)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "not complete" in spec["permissionDecisionReason"]


def test_hook_allows_by_returning_empty_dict():
    hook = make_send_email_hook(_state(_complete_review()))
    out = hook({"tool_name": "send_email", "tool_input": _email().model_dump()}, "t1", None)
    assert out == {}
