import json

import pytest

from agent_safety import (
    PermissionSet,
    ToolCall,
    ToolRegistry,
    parse_tool_calls,
    safety_context,
)

# -- PermissionSet (de)serialization --------------------------------------

def test_permission_set_round_trips_through_dict():
    ps = PermissionSet.of("filesystem.*", "network.http", deny=["filesystem.delete"])
    data = ps.to_dict()
    assert data == {"allow": ["filesystem.*", "network.http"], "deny": ["filesystem.delete"]}
    back = PermissionSet.from_dict(data)
    assert back.allows("filesystem.read")
    assert not back.allows("filesystem.delete")
    assert not back.allows("shell.exec")


def test_permission_set_from_dict_is_json_friendly():
    ps = PermissionSet.of("a.b", deny=["a.c"])
    restored = PermissionSet.from_dict(json.loads(json.dumps(ps.to_dict())))
    assert restored == ps


def test_permission_set_from_dict_tolerates_missing_keys():
    assert PermissionSet.from_dict({}).allow == frozenset()
    assert PermissionSet.from_dict({"allow": ["x"]}).allows("x")


# -- parse_tool_calls -----------------------------------------------------

def test_parse_anthropic():
    response = {"content": [
        {"type": "text", "text": "let me check"},
        {"type": "tool_use", "id": "tu_1", "name": "get_weather", "input": {"city": "Paris"}},
    ]}
    calls = parse_tool_calls("anthropic", response)
    assert calls == [ToolCall("tu_1", "get_weather", {"city": "Paris"})]


def test_parse_openai():
    response = {"choices": [{"message": {"tool_calls": [
        {"id": "call_1", "function": {"name": "get_weather",
                                      "arguments": '{"city": "Tokyo"}'}},
    ]}}]}
    calls = parse_tool_calls("openai", response)
    assert calls == [ToolCall("call_1", "get_weather", {"city": "Tokyo"})]


def test_parse_gemini():
    response = {"candidates": [{"content": {"parts": [
        {"function_call": {"name": "get_weather", "args": {"city": "Lima"}}},
    ]}}]}
    calls = parse_tool_calls("gemini", response)
    assert calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "Lima"}


def test_parse_unknown_dialect_raises():
    with pytest.raises(ValueError):
        parse_tool_calls("cohere", {})


def test_parse_then_safe_dispatch_round_trip():
    reg = ToolRegistry()

    @reg.tool("weather.read")
    def get_weather(city: str) -> str:
        return f"sunny in {city}"

    response = {"content": [
        {"type": "tool_use", "id": "tu_9", "name": "get_weather", "input": {"city": "Oslo"}},
    ]}
    with safety_context(PermissionSet.of("weather.read")):
        results = [
            reg.safe_dispatch("anthropic", c.id, c.name, c.arguments)
            for c in parse_tool_calls("anthropic", response)
        ]
    assert results[0]["content"] == "sunny in Oslo"
