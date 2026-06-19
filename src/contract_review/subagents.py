"""Subagent definitions, the dispatch seam, and context-scoping task builders.

Each subagent is an `AgentDefinition` -- the same shape the Claude Agent SDK uses
for `ClaudeAgentOptions(agents={...})`. The coordinator selects one by
`subagent_type` (the key it is registered under) and writes its entire context
into the task: the extractor sees the whole document; the risk-checker sees only
the liability slice. A subagent shares no memory with the coordinator or its
sibling, so anything not passed cannot be recovered.

`SubagentRunner` is the seam. `StubRunner` runs the subagents deterministically
offline (tests + offline demo). In `live.py`, `ClaudeRunner` runs a subagent via
the Messages API, and `build_agent_options` registers these `AgentDefinition`s on
a real `ClaudeAgentOptions` for the Agent SDK.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from contract_review.schemas import Clause

SubagentType = Literal["extractor", "risk_checker"]


@dataclass
class AgentDefinition:
    """A subagent configuration, mirroring `claude_agent_sdk.AgentDefinition`.

    These are the load-bearing fields for this example. The real SDK type carries
    more (`disallowedTools`, `skills`, `memory`, `mcpServers`, `initialPrompt`,
    `background`, `effort`, `permissionMode`). Field names match the SDK exactly,
    including its camelCase `maxTurns`, so the offline shape transfers to the real
    SDK without translation. `model` accepts an alias ("opus", "sonnet", "haiku",
    "inherit") or a full model id.
    """

    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None
    maxTurns: int | None = None  # noqa: N815 -- matches the SDK field name


EXTRACTOR_AGENT = AgentDefinition(
    description="Extract payment terms and liability clauses from one vendor contract.",
    prompt=(
        "You are extracting structured clauses from ONE vendor contract. "
        "The clauses below are already normalized. Select which are payment terms "
        "and which are liability clauses; do not invent clauses. Return JSON: "
        '{"payment_terms": [clause_id...], "liability_clauses": [clause_id...]}. '
        "Preserve clause_id exactly."
    ),
    tools=[],
    model="inherit",
)

RISK_CHECKER_AGENT = AgentDefinition(
    description="Classify liability clauses for $1M cap exposure (the semantic call only).",
    prompt=(
        "You are reviewing liability clauses from ONE contract for cap exposure "
        "(the cap is $1,000,000 USD aggregate). For each clause decide whether it is "
        "a liability/indemnity clause whose `amount` represents exposure the cap "
        "applies to. Do NOT compare numbers yourself -- the system does the "
        'arithmetic. Return JSON: {"verdicts": [{"clause_id", "page", '
        '"is_liability_exposure", "amount", "rationale"}]}, preserving clause_id and '
        "page unchanged."
    ),
    tools=[],
    model="inherit",
)

# Registered by `subagent_type`, exactly as `ClaudeAgentOptions(agents={...})`.
AGENTS: dict[str, AgentDefinition] = {
    "extractor": EXTRACTOR_AGENT,
    "risk_checker": RISK_CHECKER_AGENT,
}


@dataclass
class Task:
    """One isolated subagent invocation: the `subagent_type` selecting a registered
    `AgentDefinition`, plus the context the coordinator scoped for it.

    The two are not independent: `agent` must be the one registered under
    `subagent_type`. A mismatched pair would run the wrong subagent silently, so
    construction refuses it -- use `build_extractor_task`/`build_risk_task`.
    """

    subagent_type: SubagentType
    agent: AgentDefinition
    clauses: list[Clause]

    def __post_init__(self) -> None:
        if self.subagent_type not in AGENTS or self.agent is not AGENTS[self.subagent_type]:
            raise ValueError(
                f"Task.agent does not match subagent_type {self.subagent_type!r}; "
                "use build_extractor_task/build_risk_task or AGENTS[subagent_type]."
            )


class SubagentRunner(Protocol):
    def run(self, task: Task) -> dict: ...


class StubRunner:
    """Deterministic SubagentRunner for offline runs and tests.

    Stands in for the model so the coordinator and gate logic can be exercised
    without an API key. Records call order so sequencing can be asserted.
    """

    def __init__(self, *, extractor_result: dict, risk_result: dict):
        self._results: dict[SubagentType, dict] = {
            "extractor": extractor_result,
            "risk_checker": risk_result,
        }
        self.calls: list[SubagentType] = []

    def run(self, task: Task) -> dict:
        self.calls.append(task.subagent_type)
        return self._results[task.subagent_type]


def build_extractor_task(clauses: list[Clause]) -> Task:
    """The extractor needs the whole document to classify clauses."""
    return Task(subagent_type="extractor", agent=AGENTS["extractor"], clauses=clauses)


def build_risk_task(liability_clauses: list[Clause]) -> Task:
    """The risk-checker needs ONLY the liability clauses -- never the payment
    terms, never the full document."""
    return Task(
        subagent_type="risk_checker", agent=AGENTS["risk_checker"], clauses=liability_clauses
    )
