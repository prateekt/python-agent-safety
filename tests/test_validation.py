import pytest

from agent_safety import PermissionSet, ToolRegistry, safety_context, validate_args
from agent_safety.exceptions import GuardViolation


def _schema():
    return {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "days": {"type": "integer", "minimum": 1, "maximum": 14},
            "mode": {"enum": ["fast", "accurate"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["city"],
    }


def test_valid_args_pass():
    validate_args(_schema(), {"city": "Paris", "days": 3, "mode": "fast", "tags": ["a"]})


def test_wrong_type_raises():
    with pytest.raises(GuardViolation) as ei:
        validate_args(_schema(), {"city": 123})
    assert "city" in str(ei.value)


def test_missing_required_raises():
    with pytest.raises(GuardViolation):
        validate_args(_schema(), {"days": 2})


def test_out_of_range_raises():
    with pytest.raises(GuardViolation):
        validate_args(_schema(), {"city": "x", "days": 99})


def test_enum_violation_raises():
    with pytest.raises(GuardViolation):
        validate_args(_schema(), {"city": "x", "mode": "turbo"})


def test_array_item_type_checked():
    with pytest.raises(GuardViolation):
        validate_args(_schema(), {"city": "x", "tags": ["ok", 5]})


def test_bool_is_not_integer():
    with pytest.raises(GuardViolation):
        validate_args(_schema(), {"city": "x", "days": True})


def test_extra_props_allowed_by_default():
    # inference never sets additionalProperties:false, so extras (e.g. rationale) pass
    validate_args(_schema(), {"city": "x", "rationale": "because"})


def test_additional_properties_false_rejects_extra():
    schema = {"type": "object", "properties": {"a": {"type": "string"}},
              "additionalProperties": False}
    with pytest.raises(GuardViolation):
        validate_args(schema, {"a": "ok", "b": "nope"})


# -- registry integration -------------------------------------------------

def test_registry_validate_blocks_bad_call_via_safe_dispatch():
    reg = ToolRegistry()

    @reg.tool("weather.read", validate=True)
    def get_weather(city: str, days: int = 1) -> str:
        return f"{city}:{days}"

    with safety_context(PermissionSet.of("weather.read")):
        ok = reg.safe_dispatch("openai", "c1", "get_weather", '{"city": "Lima", "days": 2}')
        assert ok["content"] == "Lima:2"
        bad = reg.safe_dispatch("openai", "c2", "get_weather", '{"city": "Lima", "days": "lots"}')
        assert bad["role"] == "tool"
        assert "days" in bad["content"]


def test_registry_without_validate_does_not_check():
    reg = ToolRegistry()

    @reg.tool("x.run")  # validate defaults to False
    def run(n: int) -> int:
        return n

    with safety_context(PermissionSet.of("x.run")):
        # no validation -> the wrong type reaches the function (which just echoes it)
        assert reg.dispatch("run", {"n": "not-an-int"}) == "not-an-int"
