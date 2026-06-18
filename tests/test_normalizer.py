"""PostToolUse normalizer: messy pdf_extract output -> canonical clauses.

The normalizer runs BEFORE the model reasons over the clauses, and it owns the
deterministic fields. `amount` in particular must be parsed by code, never by
the model -- the entire $1M cap decision rests on it.
"""

from decimal import Decimal

from contract_review.normalizer import (
    classify_type,
    normalize_extraction,
    parse_money,
)


class TestParseMoney:
    def test_plain_dollar_amount_with_commas(self):
        assert parse_money("Liability capped at $1,000,000 total") == Decimal("1000000")

    def test_decimal_amount(self):
        assert parse_money("Fee of $2,500.50 per month") == Decimal("2500.50")

    def test_million_suffix(self):
        assert parse_money("exposure up to $1.5M") == Decimal("1500000")

    def test_thousand_suffix(self):
        assert parse_money("late fee of $500K") == Decimal("500000")

    def test_no_amount_returns_none(self):
        assert parse_money("Governing law shall be Delaware") is None

    def test_first_amount_wins_when_multiple(self):
        assert parse_money("between $750,000 and $2,000,000") == Decimal("750000")

    def test_malformed_grouping_returns_none(self):
        # "$1,00,000" is not valid grouping; degrading to Decimal("1") would be
        # more dangerous for a cap decision than returning None.
        assert parse_money("liability up to $1,00,000") is None

    def test_suffix_glued_to_a_word_is_not_money(self):
        assert parse_money("the $5Mega project") is None

    def test_decimal_with_suffix(self):
        assert parse_money("up to $0.5M") == Decimal("500000")

    def test_trailing_period_after_amount(self):
        assert parse_money("shall not exceed $5,000,000.") == Decimal("5000000")


class TestClassifyType:
    def test_payment_clause(self):
        assert classify_type("Payment is due net 30 days from invoice") == "payment"

    def test_liability_clause(self):
        assert classify_type("Total liability shall not exceed $1,000,000") == "liability"

    def test_indemnification_is_liability(self):
        assert classify_type("Vendor shall indemnify Customer for all damages") == "liability"

    def test_unrelated_clause_is_other(self):
        assert classify_type("This agreement is governed by Delaware law") == "other"


class TestNormalizeExtraction:
    def _raw(self):
        return {
            "fragments": [
                {"number": "12.1", "page": 9,
                 "text": "Total   liability\n  shall not exceed $5,000,000."},
                {"page": 3, "text": "Payment due net 30 days."},
            ]
        }

    def test_stamps_provenance_on_every_clause(self):
        clauses = normalize_extraction(
            self._raw(), contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf"
        )
        assert len(clauses) == 2
        assert all(c.source_name == "acme_msa.pdf" for c in clauses)
        assert all(c.page > 0 for c in clauses)

    def test_uses_clause_number_when_present(self):
        clauses = normalize_extraction(
            self._raw(), contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf"
        )
        assert clauses[0].clause_id == "12.1"

    def test_generates_clause_id_when_number_missing(self):
        clauses = normalize_extraction(
            self._raw(), contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf"
        )
        assert clauses[1].clause_id == "vendor-acme-msa-2026#c001"

    def test_collapses_whitespace_in_text(self):
        clauses = normalize_extraction(
            self._raw(), contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf"
        )
        assert clauses[0].text == "Total liability shall not exceed $5,000,000."

    def test_parses_amount_deterministically(self):
        clauses = normalize_extraction(
            self._raw(), contract_id="vendor-acme-msa-2026", source_name="acme_msa.pdf"
        )
        assert clauses[0].amount == Decimal("5000000")
        assert clauses[1].amount is None
