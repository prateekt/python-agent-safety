import enum
from typing import Annotated, Dict, List, Literal, Optional

from agent_safety import (
    Param,
    PermissionSet,
    ToolRegistry,
    safety_context,
    tool_description,
    tool_schema,
)


def test_primitives_and_required():
    def f(a: str, b: int, c: float, d: bool):
        ...

    s = tool_schema(f)
    assert s["properties"]["a"] == {"type": "string"}
    assert s["properties"]["b"] == {"type": "integer"}
    assert s["properties"]["c"] == {"type": "number"}
    assert s["properties"]["d"] == {"type": "boolean"}
    assert s["required"] == ["a", "b", "c", "d"]


def test_defaults_are_optional_and_captured():
    def f(a: str, b: int = 5):
        ...

    s = tool_schema(f)
    assert s["required"] == ["a"]          # b has a default -> not required
    assert s["properties"]["b"]["default"] == 5


def test_optional_unwraps_to_inner_type():
    def f(x: Optional[int] = None):
        ...

    s = tool_schema(f)
    assert s["properties"]["x"]["type"] == "integer"
    assert "required" not in s             # only optional params


def test_optional_without_default_still_required():
    def f(x: Optional[int]):
        ...

    s = tool_schema(f)
    assert s["properties"]["x"]["type"] == "integer"
    assert s["required"] == ["x"]


def test_list_and_dict():
    def f(items: List[str], mapping: Dict[str, int]):
        ...

    s = tool_schema(f)
    assert s["properties"]["items"] == {"type": "array", "items": {"type": "string"}}
    assert s["properties"]["mapping"]["type"] == "object"
    assert s["properties"]["mapping"]["additionalProperties"] == {"type": "integer"}


def test_literal_becomes_enum():
    def f(mode: Literal["fast", "accurate"]):
        ...

    s = tool_schema(f)
    assert s["properties"]["mode"]["enum"] == ["fast", "accurate"]
    assert s["properties"]["mode"]["type"] == "string"


def test_enum_class_becomes_enum():
    class Unit(enum.Enum):
        METRIC = "metric"
        IMPERIAL = "imperial"

    def f(u: Unit = Unit.METRIC):
        ...

    s = tool_schema(f)
    assert s["properties"]["u"]["enum"] == ["metric", "imperial"]
    # the default is an enum member (not JSON-safe), so it is omitted
    assert "default" not in s["properties"]["u"]


def test_annotated_description():
    def f(city: Annotated[str, "the city to look up"]):
        ...

    s = tool_schema(f)
    assert s["properties"]["city"]["description"] == "the city to look up"


def test_annotated_param_constraints():
    def f(n: Annotated[int, Param(description="count", minimum=1, maximum=10)] = 3):
        ...

    p = tool_schema(f)["properties"]["n"]
    assert p["description"] == "count"
    assert p["minimum"] == 1
    assert p["maximum"] == 10
    assert p["default"] == 3


def test_unknown_type_is_unconstrained():
    def f(x):  # no annotation
        ...

    assert tool_schema(f)["properties"]["x"] == {}


def test_skips_self_and_varargs():
    class C:
        def m(self, a: str, *args, **kwargs):
            ...

    s = tool_schema(C.m)
    assert list(s["properties"]) == ["a"]


def test_description_from_docstring_summary():
    def f(a: str):
        """Do the thing.

        A longer explanation that should be excluded.
        """

    assert tool_description(f) == "Do the thing."


def test_explicit_description_wins():
    def f(a: str):
        """Docstring summary."""

    assert tool_description(f, "explicit") == "explicit"


def test_no_docstring_yields_empty():
    def f(a: str):
        ...

    assert tool_description(f) == ""


# -- integration with ToolRegistry ---------------------------------------

def test_registry_infers_when_omitted():
    reg = ToolRegistry()

    @reg.tool("weather.read")
    def get_weather(city: str, units: str = "metric") -> str:
        """Get the weather for a city."""
        return f"sunny in {city}"

    spec = reg.schemas("anthropic")[0]
    assert spec["description"] == "Get the weather for a city."
    assert spec["input_schema"]["properties"]["city"] == {"type": "string"}
    assert spec["input_schema"]["required"] == ["city"]
    # and it still dispatches through the safety pipeline
    with safety_context(PermissionSet.of("weather.read")):
        assert reg.dispatch("get_weather", {"city": "Paris"}) == "sunny in Paris"


def test_registry_explicit_parameters_still_win():
    reg = ToolRegistry()
    explicit = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}

    @reg.tool("search.run", description="Search.", parameters=explicit)
    def search(query: str) -> str:  # signature differs from explicit schema on purpose
        return query

    spec = reg.schemas("openai")[0]["function"]
    assert spec["parameters"] == explicit
    assert spec["description"] == "Search."
