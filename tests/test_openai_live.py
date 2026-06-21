"""Optional live check against the real OpenAI API.

Skipped unless BOTH are true:
  * the ``openai`` SDK is installed, and
  * a key is present in ``OPENAI_API_KEY``.

The key is read from the environment — never hardcoded. Run locally with:

    OPENAI_API_KEY=... python -m pytest tests/test_openai_live.py -v

Model defaults to ``gpt-4o-mini``; override with ``OPENAI_MODEL``. CI does not
run this (no key, no SDK).
"""

import os

import pytest

from agent_safety import PermissionSet, ToolRegistry, safety_context

openai = pytest.importorskip("openai", reason="openai SDK not installed")

API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
pytestmark = pytest.mark.skipif(not API_KEY, reason="no OPENAI_API_KEY set")


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


def test_openai_tool_call_is_safety_dispatched():
    reg = _registry()
    client = openai.OpenAI(api_key=API_KEY)

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "What is 17 plus 25? Use the add tool."}],
        tools=reg.schemas("openai"),
    )

    tool_calls = resp.choices[0].message.tool_calls
    assert tool_calls, "model did not request the tool"
    call = tool_calls[0]

    # arguments is a JSON string — dispatch parses it. Only math.add is permitted.
    with safety_context(PermissionSet.of("math.add")):
        result = reg.dispatch(call.function.name, call.function.arguments)

    assert result == 42

    # safe_dispatch builds the provider-native tool message you'd send back.
    msg = reg.tool_result("openai", call.id, call.function.name, result)
    assert msg["role"] == "tool" and msg["tool_call_id"] == call.id
