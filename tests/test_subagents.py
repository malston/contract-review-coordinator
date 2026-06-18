"""Subagents are SDK-shaped: each is an AgentDefinition registered by name, and
the coordinator scopes its context (sufficient, not flooded).

The extractor needs the whole document to classify clauses. The risk-checker
needs ONLY the liability slice -- handing it the payment terms or the full
document would dilute attention on the clauses that actually matter. Isolation
is enforced by the harness (the task builders), not left to the model to honor.
"""

from decimal import Decimal

from contract_review.schemas import Clause
from contract_review.subagents import (
    AGENTS,
    AgentDefinition,
    build_extractor_task,
    build_risk_task,
)


def _clauses() -> list[Clause]:
    return [
        Clause(clause_id="12.1", page=9, type="liability",
               text="Total liability shall not exceed $5,000,000.",
               amount=Decimal("5000000"), source_name="acme_msa.pdf"),
        Clause(clause_id="3.2", page=3, type="payment",
               text="Payment due net 30 days.", amount=None, source_name="acme_msa.pdf"),
    ]


def test_agents_registry_maps_subagent_type_to_agent_definition():
    assert set(AGENTS) == {"extractor", "risk_checker"}
    for agent in AGENTS.values():
        assert isinstance(agent, AgentDefinition)
        assert agent.description  # the field the model uses to select a subagent
        assert agent.prompt


def test_agent_definition_mirrors_sdk_field_names():
    # Field names match claude_agent_sdk.AgentDefinition exactly, including the
    # SDK's camelCase maxTurns -- so the offline shape transfers to the real SDK.
    agent = AgentDefinition(description="d", prompt="p", tools=["Read"], maxTurns=3)
    assert agent.tools == ["Read"]
    assert agent.maxTurns == 3


def test_extractor_task_selects_extractor_and_sees_the_whole_document():
    task = build_extractor_task(_clauses())
    assert task.subagent_type == "extractor"
    assert task.agent is AGENTS["extractor"]
    assert {c.clause_id for c in task.clauses} == {"12.1", "3.2"}


def test_risk_task_selects_risk_checker_and_sees_only_liability_clauses():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    assert task.subagent_type == "risk_checker"
    assert task.agent is AGENTS["risk_checker"]
    assert {c.clause_id for c in task.clauses} == {"12.1"}


def test_risk_task_does_not_leak_payment_clauses():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    blob = (task.agent.prompt + repr(task.clauses)).lower()
    assert "3.2" not in blob
    assert "net 30" not in blob


def test_risk_task_preserves_attribution_and_amount():
    liability = [c for c in _clauses() if c.type == "liability"]
    task = build_risk_task(liability)
    clause = task.clauses[0]
    assert (clause.clause_id, clause.page, clause.amount) == ("12.1", 9, Decimal("5000000"))
