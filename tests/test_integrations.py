import json

import pytest

from agent_safety import PermissionSet, ToolRegistry, safety_context
from agent_safety.exceptions import PermissionDenied


def build_registry():
    reg = ToolRegistry()

    @reg.tool(
        "weather.read",
        description="Get weather for a city.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    def get_weather(city: str) -> str:
        return f"sunny in {city}"

    return reg


def test_anthropic_schema_shape():
    reg = build_registry()
    tools = reg.schemas("anthropic")
    assert tools[0]["name"] == "get_weather"
    assert "input_schema" in tools[0]
    assert tools[0]["input_schema"]["required"] == ["city"]


def test_openai_schema_shape():
    reg = build_registry()
    tools = reg.schemas("openai")
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "get_weather"
    assert "parameters" in tools[0]["function"]


def test_gemini_schema_shape():
    reg = build_registry()
    tools = reg.schemas("gemini")
    assert "function_declarations" in tools[0]
    assert tools[0]["function_declarations"][0]["name"] == "get_weather"


def test_unknown_dialect_raises():
    reg = build_registry()
    with pytest.raises(ValueError):
        reg.schemas("cohere")


def test_dispatch_with_dict_args_anthropic_style():
    reg = build_registry()
    with safety_context(PermissionSet.of("weather.read")):
        assert reg.dispatch("get_weather", {"city": "Paris"}) == "sunny in Paris"


def test_dispatch_with_json_string_args_openai_style():
    reg = build_registry()
    with safety_context(PermissionSet.of("weather.read")):
        out = reg.dispatch("get_weather", json.dumps({"city": "Tokyo"}))
        assert out == "sunny in Tokyo"


def test_dispatch_enforces_permissions():
    reg = build_registry()
    with safety_context(PermissionSet.deny_all()):
        with pytest.raises(PermissionDenied):
            reg.dispatch("get_weather", {"city": "Paris"})


def test_dispatch_unknown_tool():
    reg = build_registry()
    with safety_context(PermissionSet.allow_all()):
        with pytest.raises(KeyError):
            reg.dispatch("nope", {})


def test_tool_result_formats_per_dialect():
    reg = build_registry()
    a = reg.tool_result("anthropic", "tu_1", "get_weather", "sunny")
    assert a == {"type": "tool_result", "tool_use_id": "tu_1", "content": "sunny"}

    o = reg.tool_result("openai", "call_1", "get_weather", "sunny")
    assert o == {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}

    g = reg.tool_result("gemini", "ignored", "get_weather", "sunny")
    assert g["functionResponse"]["name"] == "get_weather"
    assert g["functionResponse"]["response"]["result"] == "sunny"


def test_safe_dispatch_turns_denial_into_error_result():
    reg = build_registry()
    with safety_context(PermissionSet.deny_all()):
        msg = reg.safe_dispatch("openai", "call_9", "get_weather", {"city": "Paris"})
    assert msg["role"] == "tool"
    assert "denied" in msg["content"]


def test_safe_dispatch_success():
    reg = build_registry()
    with safety_context(PermissionSet.of("weather.read")):
        msg = reg.safe_dispatch("anthropic", "tu_9", "get_weather", {"city": "Lima"})
    assert msg["content"] == "sunny in Lima"
    assert "is_error" not in msg
