"""Coordinator-owned state.

This is the harness's memory, not the model's. The model drives the loop by
emitting tool calls; the harness writes completion state here. The send_email
gate reads `reviews` from this object -- which is why a model cannot forge
completion by narrating it.
"""

from dataclasses import dataclass, field

from contract_review.schemas import Clause, Review


@dataclass
class CoordinatorState:
    contract_id: str
    source_name: str
    doc_sha256: str
    normalized_clauses: list[Clause] = field(default_factory=list)
    liability_clauses: list[Clause] = field(default_factory=list)
    extractor_completed: bool = False
    reviews: dict[str, Review] = field(default_factory=dict)
