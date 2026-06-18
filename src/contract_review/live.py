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

from contract_review.coordinator import make_pdf_extract_hook
from contract_review.gate import make_send_email_hook
from contract_review.harness import COORDINATOR_ALLOWED_TOOLS
from contract_review.loop import Response
from contract_review.state import CoordinatorState
from contract_review.subagents import AGENTS, Task

MODEL = "claude-opus-4-8"

COORDINATOR_SYSTEM = (
    "You are a contract-review coordinator. Decompose the request and use the "
    "tools. Call pdf_extract first, then Task(subagent_type='extractor'), then "
    "Task(subagent_type='risk_checker'), then send_email. You may not send before "
    "the risk review has run -- the system enforces this regardless of what you say."
)

COORDINATOR_TOOLS = [
    {
        "name": "pdf_extract",
        "description": "Extract raw clause fragments from the uploaded contract PDF.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "Task",
        "description": "Dispatch an isolated subagent. subagent_type='extractor' "
        "selects payment vs liability clauses; subagent_type='risk_checker' reviews "
        "liability clauses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subagent_type": {"type": "string", "enum": ["extractor", "risk_checker"]}
            },
            "required": ["subagent_type"],
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


def _as_async_hook(sync_hook):
    """The SDK calls hooks as coroutines; our hook cores are sync. Wrap them so the
    same gate/normalizer logic runs in both the offline loop and the real SDK."""

    async def hook(input_data: dict, tool_use_id: str, context) -> dict:
        return sync_hook(input_data, tool_use_id, context)

    return hook


def build_agent_options(
    state: CoordinatorState,
    *,
    resume: str | None = None,
    fork_session: bool = False,
    session_id: str | None = None,
):
    """Construct the real `ClaudeAgentOptions` for this coordinator.

    This is the genuine Agent SDK surface the offline implementation mirrors:
    `agents` (the two AgentDefinitions), `allowed_tools` (incl. "Task"), `hooks`
    (the send_email gate as PreToolUse, the normalizer as PostToolUse), and the
    session controls `resume` / `fork_session` / `session_id`. A full run also
    needs the custom tools (`pdf_extract`, `send_email`) registered as MCP/SDK
    tools and the CLI + API key; this function builds the configuration object
    itself. Its field names are verified against the installed `claude-agent-sdk`
    and exercised in `tests/test_live.py`.
    """
    from claude_agent_sdk import AgentDefinition as SdkAgentDefinition
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    agents = {
        name: SdkAgentDefinition(
            description=agent.description,
            prompt=agent.prompt,
            tools=agent.tools,
            model=agent.model,
            maxTurns=agent.maxTurns,
        )
        for name, agent in AGENTS.items()
    }
    send_email_gate = _as_async_hook(make_send_email_hook(state))
    pdf_extract_normalizer = _as_async_hook(make_pdf_extract_hook(state))

    return ClaudeAgentOptions(
        model=MODEL,
        agents=agents,
        allowed_tools=list(COORDINATOR_ALLOWED_TOOLS),
        hooks={
            "PreToolUse": [HookMatcher(matcher="send_email", hooks=[send_email_gate])],
            "PostToolUse": [HookMatcher(matcher="pdf_extract", hooks=[pdf_extract_normalizer])],
        },
        resume=resume,
        fork_session=fork_session,
        session_id=session_id,
    )


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


def subagent_prompt(task: Task) -> str:
    """The isolated context handed to a subagent: its agent's prompt plus the
    clauses the coordinator scoped for it."""
    clauses = json.dumps([c.model_dump(mode="json") for c in task.clauses])
    return f"{task.agent.prompt}\n\n<clauses>\n{clauses}\n</clauses>"


class ClaudeRunner:
    """SubagentRunner backed by the Messages API -- runs an isolated subagent."""

    def __init__(self, *, model: str = MODEL, max_tokens: int = 4096):
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def run(self, task: Task) -> dict:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": subagent_prompt(task)}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return parse_subagent_output(text)
