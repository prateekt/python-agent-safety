"""Optional live check against the real Gemini API.

Skipped unless BOTH are true:
  * the ``google-genai`` SDK is installed, and
  * a key is present in ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``).

The key is read from the environment — never hardcoded. Run locally with:

    GEMINI_API_KEY=... python -m pytest tests/test_gemini_live.py -v

This proves the provider-neutral pieces (schema export, dispatch under a
``safety_context``, tool-result formatting) wire up against a real tool-calling
loop. CI does not run it (no key, no SDK).
"""

import os

import pytest

from agent_safety import PermissionSet, ToolRegistry, safety_context

genai = pytest.importorskip("google.genai", reason="google-genai SDK not installed")

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
pytestmark = pytest.mark.skipif(not API_KEY, reason="no GEMINI_API_KEY/GOOGLE_API_KEY set")


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


def test_gemini_tool_call_is_safety_dispatched():
    from google.genai import types

    reg = _registry()
    client = genai.Client(api_key=API_KEY)
    tools = reg.schemas("gemini")  # [{"function_declarations": [...]}]

    config = types.GenerateContentConfig(tools=tools)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="What is 17 plus 25? Use the add tool.",
        config=config,
    )

    # Find the function call the model requested.
    call = None
    for part in resp.candidates[0].content.parts:
        if getattr(part, "function_call", None):
            call = part.function_call
            break
    assert call is not None, "model did not request the tool"

    # Dispatch it through the safety pipeline — only math.add is permitted.
    with safety_context(PermissionSet.of("math.add")):
        result = reg.dispatch(call.name, dict(call.args))

    assert result == 42
