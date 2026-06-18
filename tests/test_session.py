"""Session resumption and forking (Domain 1.7).

The store persists a *structured manifest* -- the case facts of completed work --
not the raw message/tool-result history. Resuming from structured state is more
reliable than replaying stale tool results.

  - resume: a completed review survives into a brand-new session, so the gate
    passes without re-running the pipeline.
  - stale detection: resuming against a changed document (doc_sha256 mismatch) is
    refused rather than silently trusting a review of the old document.
  - fork: two independent branches diverge from one shared analysis baseline,
    neither mutating the other.
"""

from decimal import Decimal

import pytest

from contract_review.coordinator import ingest_extraction, run_extractor, run_risk_check
from contract_review.gate import evaluate_send_email
from contract_review.schemas import EmailRequest
from contract_review.session import SessionManifest, SessionStore, StaleSessionError
from contract_review.subagents import StubRunner


def _runner(extractor_result, risk_result) -> StubRunner:
    return StubRunner(extractor_result=extractor_result, risk_result=risk_result)


def _reviewed(state, sample_raw, extractor_result, risk_result):
    """Drive the full pipeline so `state` carries a completed review."""
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    run_risk_check(state, runner)
    return state


def _email() -> EmailRequest:
    return EmailRequest(
        to="legal@acme.com", subject="Contract review",
        body="Clause 12.1 over cap.", cited_clause_ids=["12.1"],
    )


def test_resume_restores_completion_so_the_gate_passes_in_a_new_session(
    state, sample_raw, extractor_result, risk_result
):
    _reviewed(state, sample_raw, extractor_result, risk_result)
    store = SessionStore()
    store.save("review-session-1", state)

    resumed = store.resume("review-session-1", doc_sha256=state.doc_sha256)

    assert resumed is not state  # a brand-new session object
    assert evaluate_send_email(_email(), resumed).allowed is True


def test_resume_against_a_changed_document_is_refused_as_stale(
    state, sample_raw, extractor_result, risk_result
):
    _reviewed(state, sample_raw, extractor_result, risk_result)
    store = SessionStore()
    store.save("review-session-1", state)

    with pytest.raises(StaleSessionError, match="document"):
        store.resume("review-session-1", doc_sha256="sha256-a-different-contract")


def test_resume_unknown_session_raises(state):
    store = SessionStore()
    with pytest.raises(KeyError, match="unknown session"):
        store.resume("never-saved", doc_sha256=state.doc_sha256)


def test_saving_does_not_bind_the_manifest_to_later_state_mutation(
    state, sample_raw, extractor_result, risk_result
):
    _reviewed(state, sample_raw, extractor_result, risk_result)
    store = SessionStore()
    store.save("review-session-1", state)

    # Mutate the live state after saving; the saved manifest must be unaffected.
    state.reviews.clear()
    resumed = store.resume("review-session-1", doc_sha256=state.doc_sha256)
    assert evaluate_send_email(_email(), resumed).allowed is True


def test_fork_creates_independent_branches_from_a_shared_baseline(
    state, sample_raw, extractor_result, risk_result
):
    # Baseline: extraction done, risk-check NOT yet run.
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    store = SessionStore()
    store.save("baseline", state)

    store.fork("baseline", "fork-1m")
    store.fork("baseline", "fork-100k")

    # Each fork explores a different cap from the same extracted liability set.
    branch_1m = store.resume("fork-1m", doc_sha256=state.doc_sha256)
    review_1m = run_risk_check(branch_1m, _runner(extractor_result, risk_result),
                               cap=Decimal("1000000"))
    branch_100k = store.resume("fork-100k", doc_sha256=state.doc_sha256)
    review_100k = run_risk_check(branch_100k, _runner(extractor_result, risk_result),
                                 cap=Decimal("100000"))

    assert {v.clause_id for v in review_1m.flagged} == {"12.1"}          # only $5M clause
    assert {v.clause_id for v in review_100k.flagged} == {"12.1", "8.4"}  # $5M and $250k

    # The branches did not contaminate each other or the baseline.
    assert branch_1m.reviews.keys() == branch_100k.reviews.keys()
    assert branch_1m.reviews is not branch_100k.reviews
    assert store.resume("baseline", doc_sha256=state.doc_sha256).reviews == {}


def test_manifest_is_a_structured_summary_not_a_message_transcript(
    state, sample_raw, extractor_result, risk_result
):
    _reviewed(state, sample_raw, extractor_result, risk_result)
    store = SessionStore()
    manifest = store.save("review-session-1", state)

    assert isinstance(manifest, SessionManifest)
    # The summary carries structured case facts...
    assert manifest.reviews["vendor-acme-msa-2026"].status == "complete"
    assert manifest.normalized_clauses
    # ...and deliberately not a raw message / tool-result transcript.
    assert not hasattr(manifest, "messages")
