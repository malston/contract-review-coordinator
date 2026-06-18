# Domain 1 Build Exercise — Contract-Review Coordinator

You are helping me build a working multi-agent system that exercises every concept in
Domain 1 (Agentic Architecture & Orchestration) of the Claude Certified Architect exam.
Build it with me incrementally. Explain each architectural choice as you go, and push back
hard if my instructions violate the principles below.

## Build target

A **contract-review agent**. A user uploads a vendor contract (PDF) and asks:

> "Extract the payment terms and flag any liability clauses that exceed our $1M cap.
> Then email the summary to legal@acme.com."

The test the system must pass:

> The `send_email` tool must **not** fire unless the risk review has actually completed -- and
> a model that merely _claims_ "I've reviewed it" must not be able to bypass the gate. If a
> prompt-only instruction could let an unreviewed summary reach legal@acme.com, the design fails.
> The gate must read **real completion state**, not the model's narration -- this is the
> natural-language-termination anti-pattern wearing a different hat.

## Requirements — every one must be satisfied

1. **Coordinator** runs the standard agentic loop (Messages API, branch on `stop_reason`,
   append assistant turns verbatim, send tool results back as `role: "user"`). It decomposes
   the work, spawns subagents via the `Task` tool, and aggregates the results. Termination is
   `stop_reason == "end_turn"` — never natural-language matching, never an arbitrary iteration
   cap treated as completion, never text-content-as-completion.

2. **Two subagents**, dispatched correctly. Decide **parallel vs. sequential** and justify the
   choice from the actual data dependency between them:
   - **Extractor** — pulls payment terms + liability clauses from the contract.
   - **Risk-checker** — makes the _semantic_ call (is this a liability clause the cap governs?);
     the `amount > $1M` comparison is done in deterministic code. A deterministic comparison is
     only deterministic if its operand is too -- the number compared to the cap must come from
     the normalizer's parsed `amount`, never from a value the risk-checker returned.

3. **Proper context passing.** Show the `Task` prompts. Each subagent gets a clean, _sufficient_
   context — not starved (missing what it needs to do the job), not flooded (the whole document
   when it needs one slice). Pass **structured metadata** (clause number, page number, source
   name) so every flagged clause traces back to its source. Attribution must survive
   extraction → risk-check → aggregation → email. Carrying it is necessary but not sufficient:
   at each boundary **verify** the carried IDs against their source (reconcile a verdict's
   clause_id/page against the extracted clause; reject what does not match). Provenance that is
   carried but never verified can be forged by the model.

4. **Programmatic prerequisite gate.** The `send_email` tool must be **blocked** unless the
   risk review has actually completed. Enforce this with a **PreToolUse hook** that checks a
   real completion flag in state — NOT the model's claim that it finished. Sending a contract
   summary to an external party is an external/irreversible action; justify which enforcement
   category it falls into and why a prompt instruction is insufficient. An irreversible external
   action has **more than one way to be wrong** -- enumerate and gate each: not-yet-reviewed, the
   wrong recipient, and content citing clauses the review never verified -- not just the headline
   "unreviewed" case.

5. **PostToolUse hook.** The PDF-extraction tool returns messy output (clause formatting is
   inconsistent across pages). Normalize it into a canonical structure
   (e.g. `{clause_id, page, type, text, amount}`) _before_ the model reasons over it. The `amount`
   parser is load-bearing for the cap decision, so it must **fail safe**: on anything it cannot
   parse cleanly (malformed grouping, a number glued to a word) it returns a safe sentinel
   (`None` / escalate), never a plausible-but-wrong number -- Domain 5's access-failure-vs-empty
   applied to parsing.

## Principles to enforce while building (Domain 1 spine)

- **Control flow reads the protocol, not the prose.** Loop decisions key off `stop_reason`,
  block `type`, and `tool_use_id` — never the model's narration.
- **Isolation.** Subagents share no memory with the coordinator or each other. A subagent's
  entire universe is what the coordinator writes into its `Task` prompt.
- **Sufficient, not flooded.** Give each subagent exactly the context its subtask depends on.
  No less (starvation → blind wrong answers), no more (dilution → degraded attention).
- **Attribution is structural.** Provenance that isn't carried as structured metadata at
  context-passing time cannot exist in the output. You can't recover at aggregation what you
  destroyed at decomposition.
- **Enforce by consequence of failure.** Financial / security / compliance / irreversible-
  external consequences → programmatic hook (deterministic). Cosmetic/soft → prompt (probabilistic).
- **Parallel for independent work; a data dependency forces sequential.**
- **Gates check real state, not the model's say-so.** "The model said the review is done" is
  the natural-language-termination anti-pattern wearing a different hat.
- **Real state over the model's word -- everywhere, not just the gate.** That same discipline
  governs _every_ consequential decision and load-bearing value, not only the send gate.
  Enumerate each value the model supplies that feeds a decision -- the cap amount, the cited
  clauses, the provenance fields -- and show each is replaced by a deterministic source or
  verified against one.

## Deliverables

Produce, with the architecture explained:

1. The coordinator loop (pseudocode is fine — architecture over syntax).
2. The two `Task` invocations with their full prompts and structured-metadata payloads.
3. The PreToolUse gate logic for `send_email`.
4. The PostToolUse normalizer for the PDF-extraction tool.
5. A one-paragraph justification of the parallel-vs-sequential decision, naming the dependency.
6. **Executable adversarial tests** (not self-grading by inspection alone): pin the linchpin _and_
   its distractor; for each "the model never decides X" claim, a test where the model's value
   disagrees and the deterministic one must win; plus boundaries (exactly-at-cap, unparseable amount).

## How I want you to grade the result (apply this to your own output)

- **Linchpin:** does the `send_email` gate block on **real completion state**, so a model that
  only _claims_ the review is done cannot send? A prompt-only guard fails this test.
- Is parallel vs. sequential correct, with the forcing dependency named explicitly?
- Does structured metadata survive end-to-end so the emailed summary is attributable to
  clause + page?
- Does the PostToolUse normalizer run **before** the model reasons over the extracted clauses?
- Is each subagent's context sufficient but not flooded?
- Is every model-supplied value that feeds a decision replaced by, or verified against, a
  deterministic source? Trace the cap **operand** and the **cited clauses**, not just the gate flag.
- Does the gate cover **every** failure mode of the send -- unreviewed, wrong recipient, fabricated
  citation -- not only the headline one?
- Does the `amount` parser **fail safe** (sentinel, not a wrong number) on malformed input?
- Are there **executable** tests pinning each claim, including a model-disagrees / deterministic-wins
  test for the cap operand?

Build it step by step. Where my instructions would violate a principle above, stop and tell me.
