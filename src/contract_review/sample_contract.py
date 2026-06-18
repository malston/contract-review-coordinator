"""A worked sample contract for the offline demo and the tests.

One vendor MSA with: a $5M aggregate-liability clause (over the $1M cap), a
$250k data-breach cap (under), a payment term, and a governing-law clause.
EXTRACTOR_RESULT and RISK_RESULT are what the two subagents would return; the
StubRunner replays them so the whole system runs without an API key.
"""

SAMPLE_RAW = {
    "fragments": [
        {"number": "12.1", "page": 9, "text": "Total liability shall not exceed $5,000,000."},
        {"number": "8.4", "page": 6, "text": "Data-breach liability capped at $250,000."},
        {"number": "3.2", "page": 3, "text": "Payment due net 30 days."},
        {"number": "20.1", "page": 14, "text": "This agreement is governed by Delaware law."},
    ]
}

EXTRACTOR_RESULT = {"payment_terms": ["3.2"], "liability_clauses": ["12.1", "8.4"]}

RISK_RESULT = {
    "verdicts": [
        {"clause_id": "12.1", "page": 9, "is_liability_exposure": True,
         "amount": "5000000", "rationale": "Aggregate liability cap above $1M."},
        {"clause_id": "8.4", "page": 6, "is_liability_exposure": True,
         "amount": "250000", "rationale": "Data-breach cap below $1M."},
    ]
}
