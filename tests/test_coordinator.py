"""Orchestration: normalize -> extract -> risk-check -> flag -> Review.

Two things this proves:
  - The risk-check is SEQUENTIAL on the extractor: its entire input is the
    extractor's liability output, so it cannot run first.
  - The completion state the gate later reads is written here by the harness,
    deterministically, after real verdicts -- and the $1M comparison is code,
    not the model.

The sample contract lives in conftest.py.
"""

from decimal import Decimal

import pytest

from contract_review.coordinator import (
    compose_email,
    flag_over_cap,
    ingest_extraction,
    run_extractor,
    run_risk_check,
)
from contract_review.schemas import Verdict
from contract_review.subagents import StubRunner


def _runner(extractor_result, risk_result) -> StubRunner:
    return StubRunner(extractor_result=extractor_result, risk_result=risk_result)


def test_risk_check_requires_extractor_to_run_first(
    state, sample_raw, extractor_result, risk_result
):
    ingest_extraction(state, sample_raw)
    with pytest.raises(RuntimeError, match="extractor"):
        run_risk_check(state, _runner(extractor_result, risk_result))  # extractor never ran


def test_subagents_run_in_extractor_then_risk_order(
    state, sample_raw, extractor_result, risk_result
):
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    run_risk_check(state, runner)
    assert runner.calls == ["extractor", "risk_checker"]


def test_risk_check_writes_real_completion_state(
    state, sample_raw, extractor_result, risk_result
):
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    run_risk_check(state, runner)
    review = state.reviews["vendor-acme-msa-2026"]
    assert review.status == "complete"
    assert review.verified_clause_ids == {"12.1", "8.4"}


def test_over_cap_clause_is_flagged_under_cap_is_not(
    state, sample_raw, extractor_result, risk_result
):
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    review = run_risk_check(state, runner)
    assert {v.clause_id for v in review.flagged} == {"12.1"}  # 8.4 is $250k, under cap


def test_clause_marked_non_exposure_is_never_flagged_even_if_large(
    state, sample_raw, extractor_result
):
    # The semantic gate is respected: a huge amount the subagent says is NOT
    # liability exposure must not be flagged.
    risk = {"verdicts": [
        {"clause_id": "12.1", "page": 9, "is_liability_exposure": False,
         "amount": "5000000", "rationale": "This is a payment schedule, not liability."},
        {"clause_id": "8.4", "page": 6, "is_liability_exposure": True,
         "amount": "250000", "rationale": "Below cap."},
    ]}
    runner = _runner(extractor_result, risk)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    review = run_risk_check(state, runner)
    assert review.flagged == []


def test_compose_email_carries_attribution_to_clause_page_and_source(
    state, sample_raw, extractor_result, risk_result
):
    runner = _runner(extractor_result, risk_result)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    run_risk_check(state, runner)
    email = compose_email(state)
    assert email.to == "legal@acme.com"
    assert email.cited_clause_ids == ["12.1"]
    assert "12.1" in email.body
    assert "p.9" in email.body
    assert "acme_msa.pdf" in email.body
    assert "5000000" in email.body  # the over-cap amount is rendered


def test_cap_uses_clause_amount_not_the_models_verdict_amount(
    state, sample_raw, extractor_result
):
    # The risk subagent lowballs the amount in its verdict; the deterministic
    # clause amount ($5,000,000 for clause 12.1) must still drive the flag.
    risk = {"verdicts": [
        {"clause_id": "12.1", "page": 1, "is_liability_exposure": True,
         "amount": "1", "rationale": "model lowballed the number"},
        {"clause_id": "8.4", "page": 1, "is_liability_exposure": True,
         "amount": "1", "rationale": "model lowballed the number"},
    ]}
    runner = _runner(extractor_result, risk)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    review = run_risk_check(state, runner)
    assert {v.clause_id for v in review.flagged} == {"12.1"}
    flagged = {v.clause_id: v for v in review.flagged}
    assert flagged["12.1"].amount == Decimal("5000000")  # from the clause, not "1"
    assert flagged["12.1"].page == 9  # from the clause, not the verdict's 1


def test_rejects_verdict_for_unknown_clause_id(state, sample_raw, extractor_result):
    risk = {"verdicts": [
        {"clause_id": "99.9", "page": 1, "is_liability_exposure": True,
         "amount": "5000000", "rationale": "fabricated clause"},
    ]}
    runner = _runner(extractor_result, risk)
    ingest_extraction(state, sample_raw)
    run_extractor(state, runner)
    with pytest.raises(RuntimeError, match="unknown clause_id"):
        run_risk_check(state, runner)


def test_run_extractor_requires_ingest_first(state, extractor_result, risk_result):
    runner = _runner(extractor_result, risk_result)
    with pytest.raises(RuntimeError, match="normalized clauses"):
        run_extractor(state, runner)


def test_flag_over_cap_excludes_amount_exactly_at_cap():
    v = Verdict(clause_id="x", page=1, is_liability_exposure=True,
                amount=Decimal("1000000"), rationale="exactly at the cap")
    assert flag_over_cap([v]) == []  # the cap is a strict >


def test_flag_over_cap_skips_clause_with_no_parseable_amount():
    v = Verdict(clause_id="x", page=1, is_liability_exposure=True,
                amount=None, rationale="exposure but no number")
    assert flag_over_cap([v]) == []
