"""PostToolUse normalizer for the `pdf_extract` tool.

Runs after extraction and before the model reasons over the clauses. It owns the
deterministic fields -- `clause_id`, `page`, `text`, `amount` -- so the model
never has to parse a dollar figure by eye. Semantic typing (payment vs.
liability) is a heuristic here and is authoritatively refined by the extractor
subagent downstream.
"""

import re
from decimal import Decimal

from contract_review.schemas import Clause, ClauseType

# Requires a leading `$` so bare numbers ("net 30 days") are not read as money.
# The trailing lookahead rejects a number/suffix glued to more digits, a comma, or
# a letter -- so malformed grouping ("$1,00,000") and word-glued suffixes ("$5Mega")
# fail to None rather than silently parsing a wrong amount the cap rests on.
_AMOUNT = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*([MK])?(?![A-Za-z\d,])",
    re.IGNORECASE,
)
_LIABILITY_WORDS = ("liabilit", "indemnif", "damages", "indemnit")
_PAYMENT_WORDS = ("payment", "payable", "net 30", "net 60", "invoice", "fee")


def parse_money(text: str) -> Decimal | None:
    """Parse the first monetary amount in `text` into a Decimal, or None.

    Handles comma grouping, decimals, and M/K suffixes. Deterministic by design:
    the cap decision must not depend on the model's reading of a number.
    """
    match = _AMOUNT.search(text)
    if match is None:
        return None
    whole, frac, suffix = match.groups()
    amount = Decimal(whole.replace(",", ""))
    if frac is not None:
        amount += Decimal(f"0.{frac}")
    if suffix:
        amount *= Decimal(1_000_000 if suffix.upper() == "M" else 1_000)
    return amount


def classify_type(text: str) -> ClauseType:
    """Provisional, deterministic clause typing by keyword."""
    lowered = text.lower()
    if any(word in lowered for word in _LIABILITY_WORDS):
        return "liability"
    if any(word in lowered for word in _PAYMENT_WORDS):
        return "payment"
    return "other"


def normalize_extraction(
    raw: dict, *, contract_id: str, source_name: str
) -> list[Clause]:
    """Turn messy per-page fragments into canonical, attributable clauses."""
    clauses: list[Clause] = []
    for index, fragment in enumerate(raw["fragments"]):
        text = " ".join(fragment["text"].split())
        clause_id = fragment.get("number") or f"{contract_id}#c{index:03d}"
        clauses.append(
            Clause(
                clause_id=clause_id,
                page=fragment["page"],
                type=classify_type(text),
                text=text,
                amount=parse_money(text),
                source_name=source_name,
            )
        )
    return clauses
