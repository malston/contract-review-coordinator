"""Optional live path: drive the loop and subagents against the real API.

Opt-in via `poetry install --with live` and an `ANTHROPIC_API_KEY`. The entire
test suite and the offline demo run without any of this -- `StubRunner` and
`ScriptedClient` cover the deterministic seam. `anthropic` is imported lazily so
`parse_subagent_output` (the one piece with real logic) works without it.

Model and request shape follow the current API: `claude-opus-4-8` with adaptive
thinking.
"""

import json
import re

from contract_review.loop import Response
from contract_review.subagents import Task

MODEL = "claude-opus-4-8"

COORDINATOR_SYSTEM = (
    "You are a contract-review coordinator. Decompose the request and use the "
    "tools. Call pdf_extract first, then Task(role='extractor'), then "
    "Task(role='risk_checker'), then send_email. You may not send before the "
    "risk review has run -- the system enforces this regardless of what you say."
)

COORDINATOR_TOOLS = [
    {
        "name": "pdf_extract",
        "description": "Extract raw clause fragments from the uploaded contract PDF.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "Task",
        "description": "Dispatch an isolated subagent. role='extractor' selects "
        "payment vs liability clauses; role='risk_checker' reviews liability clauses.",
        "input_schema": {
            "type": "object",
            "properties": {"role": {"type": "string", "enum": ["extractor", "risk_checker"]}},
            "required": ["role"],
        },
    },
    {
        "name": "send_email",
        "description": "Email the contract-review summary to legal. Blocked until "
        "the risk review has completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cited_clause_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["to", "subject", "body", "cited_clause_ids"],
        },
    },
]


def parse_subagent_output(text: str) -> dict:
    """Extract the JSON object a subagent returns, tolerating fences and prose."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object found in subagent output")
    return json.loads(match.group(0))


class ClaudeClient:
    """ModelClient backed by the Messages API -- drives the coordinator loop."""

    def __init__(self, *, model: str = MODEL, max_tokens: int = 4096):
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def create(self, messages: list[dict]) -> Response:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=COORDINATOR_SYSTEM,
            tools=COORDINATOR_TOOLS,
            thinking={"type": "adaptive"},
            messages=messages,
        )
        # model_dump each block so the loop can replay the assistant turn verbatim
        # (thinking and tool_use blocks preserved) on the next request.
        return Response(message.stop_reason, [block.model_dump() for block in message.content])


class ClaudeRunner:
    """SubagentRunner backed by the Messages API -- runs an isolated subagent."""

    def __init__(self, *, model: str = MODEL, max_tokens: int = 4096):
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def run(self, task: Task) -> dict:
        clauses = json.dumps([c.model_dump(mode="json") for c in task.clauses])
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            messages=[{
                "role": "user",
                "content": f"{task.instruction}\n\n<clauses>\n{clauses}\n</clauses>",
            }],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return parse_subagent_output(text)
