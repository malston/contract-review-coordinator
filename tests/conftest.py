"""Test fixtures over the shared sample contract (contract_review.sample_contract)."""

import pytest

from contract_review.sample_contract import EXTRACTOR_RESULT, RISK_RESULT, SAMPLE_RAW
from contract_review.state import CoordinatorState


@pytest.fixture
def sample_raw() -> dict:
    return SAMPLE_RAW


@pytest.fixture
def extractor_result() -> dict:
    return EXTRACTOR_RESULT


@pytest.fixture
def risk_result() -> dict:
    return RISK_RESULT


@pytest.fixture
def state() -> CoordinatorState:
    return CoordinatorState(
        contract_id="vendor-acme-msa-2026",
        source_name="acme_msa.pdf",
        doc_sha256="sha256-acme",
    )
