"""The runnable offline demo: two model trajectories, opposite outcomes."""

from contract_review.demo import (
    DISTRACTOR_SCRIPT,
    HAPPY_SCRIPT,
    run_trajectory,
)


def test_happy_trajectory_sends_to_legal():
    outcome = run_trajectory("happy", HAPPY_SCRIPT)
    assert outcome.sent == ["legal@acme.com"]
    assert outcome.blocked == []


def test_distractor_trajectory_is_blocked_and_sends_nothing():
    outcome = run_trajectory("distractor", DISTRACTOR_SCRIPT)
    assert outcome.sent == []
    assert any("not complete" in reason for reason in outcome.blocked)
