"""Subagent dispatch seam and context-scoping task builders.

A subagent is isolated: its entire universe is the Task the coordinator writes
for it. So the coordinator decides -- structurally -- exactly which clauses each
subagent sees. The extractor sees the whole document; the risk-checker sees only
the liability slice.

`SubagentRunner` is the seam. `StubRunner` runs the subagents deterministically
offline (used by the tests and the offline demo); `ClaudeRunner` (in `live.py`)
runs them against the real Messages API.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from contract_review.schemas import Clause

SubagentRole = Literal["extractor", "risk_checker"]

EXTRACTOR_INSTRUCTION = (
    "You are extracting structured clauses from ONE vendor contract. "
    "The clauses below are already normalized. Select which are payment terms "
    "and which are liability clauses; do not invent clauses. Return JSON: "
    '{"payment_terms": [clause_id...], "liability_clauses": [clause_id...]}. '
    "Preserve clause_id exactly."
)

RISK_INSTRUCTION = (
    "You are reviewing liability clauses from ONE contract for cap exposure "
    "(the cap is $1,000,000 USD aggregate). For each clause decide whether it is "
    "a liability/indemnity clause whose `amount` represents exposure the cap "
    "applies to. Do NOT compare numbers yourself -- the system does the "
    'arithmetic. Return JSON: {"verdicts": [{"clause_id", "page", '
    '"is_liability_exposure", "amount", "rationale"}]}, preserving clause_id and '
    "page unchanged."
)


@dataclass
class Task:
    """The complete, isolated context handed to one subagent."""

    role: SubagentRole
    instruction: str
    clauses: list[Clause]


class SubagentRunner(Protocol):
    def run(self, task: Task) -> dict: ...


class StubRunner:
    """Deterministic SubagentRunner for offline runs and tests.

    Stands in for the model so the coordinator and gate logic can be exercised
    without an API key. Records call order so sequencing can be asserted.
    """

    def __init__(self, *, extractor_result: dict, risk_result: dict):
        self._results: dict[SubagentRole, dict] = {
            "extractor": extractor_result,
            "risk_checker": risk_result,
        }
        self.calls: list[SubagentRole] = []

    def run(self, task: Task) -> dict:
        self.calls.append(task.role)
        return self._results[task.role]


def build_extractor_task(clauses: list[Clause]) -> Task:
    """The extractor needs the whole document to classify clauses."""
    return Task(role="extractor", instruction=EXTRACTOR_INSTRUCTION, clauses=clauses)


def build_risk_task(liability_clauses: list[Clause]) -> Task:
    """The risk-checker needs ONLY the liability clauses -- never the payment
    terms, never the full document."""
    return Task(role="risk_checker", instruction=RISK_INSTRUCTION, clauses=liability_clauses)
