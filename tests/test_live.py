"""The only logic in the live path worth testing offline: pulling the subagent's
JSON out of model text. The network wrappers (ClaudeClient/ClaudeRunner) are a
thin, key-gated boundary and are exercised against the real API, not mocks.
"""

import pytest

from contract_review.live import parse_subagent_output


def test_parses_clean_json():
    out = parse_subagent_output('{"liability_clauses": ["12.1", "8.4"]}')
    assert out == {"liability_clauses": ["12.1", "8.4"]}


def test_parses_json_inside_code_fences():
    out = parse_subagent_output('```json\n{"verdicts": []}\n```')
    assert out == {"verdicts": []}


def test_parses_json_with_surrounding_prose():
    out = parse_subagent_output('Here is the result:\n{"a": [1, 2]}\nLet me know.')
    assert out == {"a": [1, 2]}


def test_raises_when_no_json_object_present():
    with pytest.raises(ValueError, match="JSON"):
        parse_subagent_output("I could not complete the task.")
