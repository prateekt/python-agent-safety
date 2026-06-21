"""Optional live check against the real Anthropic (Claude) API.

Skipped unless BOTH are true:
  * the ``anthropic`` SDK is installed, and
  * a key is present in ``ANTHROPIC_API_KEY``.

The key is read from the environment — never hardcoded. Run locally with:

    ANTHROPIC_API_KEY=... python -m pytest tests/test_anthropic_live.py -v

Model defaults to ``claude-opus-4-8``; override with ``ANTHROPIC_MODEL``. CI does
not run this (no key, no SDK).
"""

import os

import pytest

from agent_safety import PermissionSet, ToolRegistry, safety_context

anthropic = pytest.importorskip("anthropic", reason="anthropic SDK not installed")

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
pytestmark = pytest.mark.skipif(not API_KEY, reason="no ANTHROPIC_API_KEY set")


def _registry():
    reg = ToolRegistry()

    @reg.tool(
        "math.add",
        description="Add two integers and return the sum.",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    def add(a: int, b: int) -> int:
        return a + b

    return reg


def test_anthropic_tool_call_is_safety_dispatched():
    reg = _registry()
    client = anthropic.Anthropic(api_key=API_KEY)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=reg.schemas("anthropic"),
        messages=[{"role": "user", "content": "What is 17 plus 25? Use the add tool."}],
    )

    # Find the tool_use block: name + dict input.
    call = next((b for b in resp.content if b.type == "tool_use"), None)
    assert call is not None, "model did not request the tool"

    with safety_context(PermissionSet.of("math.add")):
        result = reg.dispatch(call.name, call.input)

    assert result == 42

    # Build the user-turn tool_result block you'd send back to continue the loop.
    block = reg.tool_result("anthropic", call.id, call.name, result)
    assert block == {"type": "tool_result", "tool_use_id": call.id, "content": "42"}
