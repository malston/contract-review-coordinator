"""Coordinator orchestration steps.

These are the deterministic actions behind the agentic loop's tools. The model
decides *when* to call them; the harness decides *what they do* -- including the
two decisions that must never be the model's: the $1M comparison, and writing
the review-complete state the send_email gate reads.

The risk-check is sequential on the extractor: its input is the extractor's
liability output, so it raises if the extractor has not run.
"""

from decimal import Decimal

from contract_review.gate import LEGAL_RECIPIENT
from contract_review.normalizer import normalize_extraction
from contract_review.schemas import Clause, EmailRequest, Review, Verdict
from contract_review.state import CoordinatorState
from contract_review.subagents import (
    SubagentRunner,
    build_extractor_task,
    build_risk_task,
)

CAP = Decimal("1000000")


def ingest_extraction(state: CoordinatorState, raw: dict) -> list[Clause]:
    """pdf_extract + PostToolUse normalizer: store canonical clauses on state."""
    clauses = normalize_extraction(
        raw, contract_id=state.contract_id, source_name=state.source_name
    )
    state.normalized_clauses = clauses
    return clauses


def run_extractor(state: CoordinatorState, runner: SubagentRunner) -> dict:
    """Dispatch the extractor subagent over the whole document; record which
    clauses it selected as liability clauses (its semantic call)."""
    if not state.normalized_clauses:
        raise RuntimeError(
            "extractor requires normalized clauses; run ingest_extraction first."
        )
    result = runner.run(build_extractor_task(state.normalized_clauses))
    by_id = {clause.clause_id: clause for clause in state.normalized_clauses}
    state.liability_clauses = [by_id[cid] for cid in result["liability_clauses"]]
    state.extractor_completed = True
    return result


def flag_over_cap(verdicts: list[Verdict], cap: Decimal = CAP) -> list[Verdict]:
    """Deterministic cap comparison -- the model never decides amount > cap."""
    return [
        v
        for v in verdicts
        if v.is_liability_exposure and v.amount is not None and v.amount > cap
    ]


def run_risk_check(
    state: CoordinatorState, runner: SubagentRunner, cap: Decimal = CAP
) -> Review:
    """Dispatch the risk-checker over the liability slice, flag over-cap clauses,
    and write real completion state for the gate to read.

    The subagent supplies only the semantic call (is_liability_exposure) and a
    rationale; `amount` and `page` are taken from the deterministic normalized
    clause, not the model's verdict, so the cap comparison and provenance never
    depend on the model's reading. A verdict for a clause that was not extracted
    is rejected rather than trusted.
    """
    if not state.extractor_completed:
        raise RuntimeError(
            "risk-check requires the extractor to run first; its input is the "
            "extractor's liability output."
        )
    result = runner.run(build_risk_task(state.liability_clauses))
    by_id = {clause.clause_id: clause for clause in state.liability_clauses}
    verdicts: list[Verdict] = []
    for raw in result["verdicts"]:
        clause = by_id.get(raw["clause_id"])
        if clause is None:
            raise RuntimeError(
                "risk-checker returned a verdict for an unknown clause_id: "
                f"{raw['clause_id']!r} (not in the extracted liability set)."
            )
        verdicts.append(
            Verdict(
                clause_id=clause.clause_id,
                page=clause.page,
                is_liability_exposure=raw["is_liability_exposure"],
                amount=clause.amount,
                rationale=raw["rationale"],
            )
        )
    review = Review(
        contract_id=state.contract_id,
        doc_sha256=state.doc_sha256,
        status="complete",
        verified_clause_ids={v.clause_id for v in verdicts},
        flagged=flag_over_cap(verdicts, cap),
    )
    state.reviews[state.contract_id] = review
    return review


def compose_email(state: CoordinatorState) -> EmailRequest:
    """Aggregate flagged clauses into an attributable summary for legal."""
    review = state.reviews[state.contract_id]
    if review.flagged:
        lines = [
            f"- Clause {v.clause_id} (p.{v.page}, {state.source_name}): "
            f"exposure ${v.amount} exceeds the $1,000,000 cap."
            for v in review.flagged
        ]
        body = "Liability clauses exceeding the $1M cap:\n" + "\n".join(lines)
    else:
        body = "No liability clauses exceed the $1,000,000 cap."
    return EmailRequest(
        to=LEGAL_RECIPIENT,
        subject=f"Contract review: {state.source_name}",
        body=body,
        cited_clause_ids=[v.clause_id for v in review.flagged],
    )
