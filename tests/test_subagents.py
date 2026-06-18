"""Subagent context scoping: sufficient, not flooded.

The extractor needs the whole document to classify clauses. The risk-checker
needs ONLY the liability slice -- handing it the payment terms or the full
document would dilute attention on the clauses that actually matter. Isolation
is enforced by the harness (the task builders), not left to the model to honor.
"""

from decimal import Decimal

from contract_review.schemas import Clause
from contract_review.subagents import build_extractor_task, build_risk_task


def _clauses() -> list[Clause]:
    return [
        Clause(clause_id="12.1", page=9, type="liability",
               text="Total liability shall not exceed $5,000,000.",
               amount=Decimal("5000000"), source_name="acme_msa.pdf"),
        Clause(clause_id="3.2", page=3, type="payment",
               text="Payment due net 30 days.", amount=None, source_name="acme_msa.pdf"),
    ]


def test_extractor_sees_the_whole_document():
    task = build_extractor_task(_clauses())
    assert task.role == "extractor"
    assert {c.clause_id for c in task.clauses} == {"12.1", "3.2"}


def test_risk_checker_sees_only_liability_clauses():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    assert task.role == "risk_checker"
    assert {c.clause_id for c in task.clauses} == {"12.1"}


def test_risk_task_does_not_leak_payment_clauses():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    blob = (task.instruction + repr(task.clauses)).lower()
    assert "3.2" not in blob
    assert "net 30" not in blob


def test_risk_task_preserves_attribution_and_amount():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    clause = task.clauses[0]
    assert (clause.clause_id, clause.page, clause.amount) == ("12.1", 9, Decimal("5000000"))
