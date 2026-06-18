"""PreToolUse gate for the `send_email` tool -- the linchpin.

Emailing a contract summary to outside counsel is an external, irreversible,
compliance-bearing action. By "enforce by consequence of failure," that demands
a deterministic programmatic gate, not a prompt instruction: a prose guard is
probabilistic and, worse, the attacker's text can live inside the very contract
being processed.

The gate reads real completion state (a Review written by the coordinator after
the risk-checker returned) -- never the model's claim that it finished. "The
model said the review is done" is the natural-language-termination anti-pattern
wearing a compliance hat.
"""

from dataclasses import dataclass

from contract_review.schemas import EmailRequest
from contract_review.state import CoordinatorState


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""


# The only party permitted to receive a contract summary. The gate owns this
# policy; the coordinator's compose_email uses the same constant.
LEGAL_RECIPIENT = "legal@acme.com"


def pre_tool_send_email(email: EmailRequest, state: CoordinatorState) -> GateDecision:
    # 1. The summary may only go to the designated legal recipient -- a fully
    #    reviewed summary sent to the wrong party is still a compliance failure.
    if email.to != LEGAL_RECIPIENT:
        return GateDecision(
            False,
            f"send_email blocked: recipient {email.to!r} is not the permitted legal recipient.",
        )

    review = state.reviews.get(state.contract_id)

    # 2. Real completion state -- set by the coordinator after a valid verdict,
    #    not settable by the model and not parsed from prose.
    if review is None or review.status != "complete":
        return GateDecision(
            False, f"send_email blocked: risk review not complete for {state.contract_id}."
        )

    # 3. Bind the review to THIS document -- a review of doc A must not authorize
    #    emailing a summary of doc B.
    if review.doc_sha256 != state.doc_sha256:
        return GateDecision(
            False, "send_email blocked: review does not match the current document."
        )

    # 4. Every clause cited in the outgoing email must exist in the verified set.
    #    Closes the path where the model fabricates a summary citing clauses the
    #    reviewer never saw.
    cited = set(email.cited_clause_ids)
    if not cited.issubset(review.verified_clause_ids):
        return GateDecision(
            False, "send_email blocked: email cites clauses absent from the verified review."
        )

    return GateDecision(True)
