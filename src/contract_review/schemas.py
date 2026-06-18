"""Canonical data structures carried end-to-end.

Provenance (`clause_id`, `page`, `source_name`) is attached the moment a clause
is normalized and is *required* on every downstream object. Attribution that is
not carried as structured metadata at context-passing time cannot be recovered
at aggregation -- so it is structural here, not optional.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

ClauseType = Literal["payment", "liability", "other"]


class Clause(BaseModel):
    """A single contract clause in canonical form (normalizer output)."""

    clause_id: str
    page: int
    type: ClauseType
    text: str
    amount: Decimal | None = None
    source_name: str


class Verdict(BaseModel):
    """A risk-checker judgment about one liability clause.

    The subagent makes the *semantic* call (`is_liability_exposure`); the
    coordinator does the arithmetic against the cap. `clause_id` and `page` are
    preserved unchanged so the verdict traces back to its source clause.
    """

    clause_id: str
    page: int
    is_liability_exposure: bool
    amount: Decimal | None = None
    rationale: str


class Review(BaseModel):
    """Real completion state for one contract's risk review.

    Written by the coordinator only after the risk-checker returns schema-valid
    verdicts. The `send_email` gate reads `status`, `doc_sha256`, and
    `verified_clause_ids` from here -- never the model's narration.
    """

    contract_id: str
    doc_sha256: str
    status: Literal["pending", "complete"] = "pending"
    verified_clause_ids: set[str] = Field(default_factory=set)
    flagged: list[Verdict] = Field(default_factory=list)


class EmailRequest(BaseModel):
    """Input to the `send_email` tool.

    `cited_clause_ids` is structured provenance, not prose: the gate checks that
    every cited clause exists in the verified review set.
    """

    to: str
    subject: str
    body: str
    cited_clause_ids: list[str] = Field(default_factory=list)
