# Domain 1 -- Contract-Review Coordinator

A runnable, test-driven implementation of the CCA Domain 1 build exercise. A
coordinator decomposes a contract-review request, dispatches two isolated
subagents (extractor, then risk-checker), and emails a summary to legal -- but
the `send_email` tool is **blocked unless the risk review has actually
completed**. A model that merely _claims_ "I've reviewed it" cannot send.

- The design doc this implements: [`../deliverables/domain1-build-exercise.md`](../deliverables/domain1-build-exercise.md)
- The exercise prompt: [`../.prompts/domain1-build-exercise.prompt.md`](../.prompts/domain1-build-exercise.prompt.md)

## Quick start

```bash
poetry install --with dev
poetry run pytest                      # NO API key needed
poetry run python -m contract_review.demo
```

The demo runs the same system along two model trajectories:

```
=== happy ===
  SENT to legal@acme.com
=== distractor ===
  nothing sent
  BLOCKED: send_email blocked: risk review not complete for vendor-acme-msa-2026.
```

The only difference between them is whether the risk-check actually ran. The
model's narration ("I have fully reviewed the contract") changes nothing.

## The linchpin

`send_email` to legal is an external, irreversible, compliance-bearing action,
so it is gated **programmatically**, not by a prompt. The PreToolUse gate
(`gate.py`) reads a real `Review` object from coordinator state -- written only
after the risk-checker returns schema-valid verdicts (`coordinator.run_risk_check`)
-- never the model's claim. "The model said the review is done" is the
natural-language-termination anti-pattern wearing a compliance hat, and the gate
refuses it. See `tests/test_gate.py` and `tests/test_end_to_end.py`.

## How the five deliverables map to code

| Deliverable                              | Where                                     | Correct pattern (demonstrated)                                                             | Distractor (shown failing)                                |
| ---------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| 1. Coordinator loop                      | `loop.py`                                 | Terminates only on `stop_reason == "end_turn"`; the step cap _raises_                      | Text saying "done" ends the loop; cap reported as success |
| 2. Two subagents, dispatched correctly   | `subagents.py`, `coordinator.py`          | **Sequential**: risk-check consumes the extractor's liability output                       | Run in parallel and risk-check nothing                    |
| 3. Context passing + structured metadata | `subagents.build_risk_task`, `schemas.py` | Risk-checker sees only the liability slice; `clause_id`+`page`+`source` survive end-to-end | Flood it with the whole document; lose provenance         |
| 4. Programmatic prerequisite gate        | `gate.py` (PreToolUse)                    | Reads real completion state in `Review`                                                    | Trust the model's "I reviewed it"                         |
| 5. PostToolUse normalizer                | `normalizer.py`, `harness.py`             | Canonicalizes + parses `amount` deterministically _before_ the model reasons               | Let the model eyeball "$1M"                               |

The parallel-vs-sequential justification (deliverable 5 in the design doc): the
risk-checker's entire input is the extractor's `liability_clauses` output, so it
**cannot** run first. That data dependency forces sequential dispatch, and the
coordinator enforces it structurally -- `run_risk_check` raises if the extractor
has not run (`tests/test_coordinator.py::test_risk_check_requires_extractor_to_run_first`).

## Two decisions the model never makes

Both are refinements the design doc calls out, enforced here in code:

- **The `$1M` comparison _and its operand_.** The risk-checker subagent makes only
  the _semantic_ call (is this clause liability exposure the cap governs?); the
  `amount` and `page` are taken from the deterministic normalized `Clause`, not the
  model's verdict, and `coordinator.flag_over_cap` does the `amount > cap` arithmetic.
  A clause the subagent marks non-exposure is never flagged; a verdict for a clause
  that was never extracted is rejected, not trusted.
- **Writing review-complete state.** Only `run_risk_check` sets it, after real
  verdicts. The model has no tool that can.
- **Where the email goes.** The gate also blocks any recipient other than
  `LEGAL_RECIPIENT` -- a fully reviewed summary sent to the wrong party is still a
  compliance failure.

## Scope boundary -- numeric caps only (by design)

This example handles caps stated as a concrete number (`$5,000,000 > $1M`), where
the comparison is deterministic arithmetic. It deliberately does **not** handle
caps expressed as a formula or relative term -- "liability shall not exceed the
fees paid in the trailing 12 months," "2x annual contract value" -- or
qualitative caps ("uncapped for confidentiality breaches").

Note the asymmetry this creates: a clause with no parseable amount becomes
`amount = None` and is never flagged, so a numeric-only reviewer silently passes
exactly the most dangerous clauses (an uncapped indemnity has no number). That
gap is intentional here, not an oversight -- resolving it belongs to other
domains, because the hard parts are theirs:

- **Domain 2 (Tool Design):** a formula cap needs an external-data tool
  (`resolve_cap_basis` -> fetch the fee schedule / contract value), and that
  tool's responses must distinguish an **access failure** (service unreachable
  -> escalate) from a **valid empty result** (genuinely no fees -> `$0`). The
  confident lie to avoid is collapsing "unreachable" into "fees = $0," which
  makes an unbounded cap look like zero exposure.
- **Domain 5 (Reliability):** an unresolved cap is a load-bearing unknown. It
  must **escalate**, never become a clean "no exposure" verdict that clears the
  send gate. The gate's definition of "complete" would grow a third state
  (`needs-confirmation` / `unresolved`) instead of an invisible `None`.

The hook for building the tool side is flagged in the Domain 2 build exercise.

## Module guide

| Module           | Responsibility                                                                  |
| ---------------- | ------------------------------------------------------------------------------- |
| `loop.py`        | Model-agnostic agentic loop + `ModelClient` seam (`ScriptedClient` offline)     |
| `harness.py`     | Wires `pdf_extract` / `Task` / `send_email` tools + the two hooks onto the loop |
| `coordinator.py` | Orchestration steps: ingest, extract, risk-check, flag, compose                 |
| `gate.py`        | The `send_email` PreToolUse gate (the linchpin)                                 |
| `normalizer.py`  | The `pdf_extract` PostToolUse normalizer (deterministic `amount`)               |
| `subagents.py`   | `Task`, the runner seam, `StubRunner`, context-scoped task builders             |
| `schemas.py`     | `Clause`, `Verdict`, `Review`, `EmailRequest` (provenance is required)          |
| `state.py`       | `CoordinatorState` -- the harness's memory, not the model's                     |
| `live.py`        | Optional `ClaudeClient` / `ClaudeRunner` against the real Messages API          |
| `demo.py`        | The two-trajectory offline demonstration                                        |

## The live path (optional)

```bash
poetry install --with dev --with live
export ANTHROPIC_API_KEY=...           # or cp .env.example .env
```

`ClaudeClient` and `ClaudeRunner` (`live.py`) drive the loop and subagents
against `claude-opus-4-8` with adaptive thinking. The deterministic seam
(`StubRunner` + `ScriptedClient`) powers every test, so the suite never needs a
key.
