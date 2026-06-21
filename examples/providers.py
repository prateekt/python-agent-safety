"""One safety policy, three providers.

Shows that the *same* guarded tools and the *same* ``safety_context`` drive a
Claude, OpenAI, or Gemini tool-calling loop unchanged — only the schema dialect
and the parsing of the model's tool call differ, and ``ToolRegistry`` absorbs
both. No network calls here; we simulate each provider's tool-call shape.

    python examples/providers.py
"""

from agent_safety import (
    ListSink,
    PermissionSet,
    Quota,
    RedactPII,
    ToolRegistry,
    safety_context,
)

registry = ToolRegistry()


@registry.tool(
    "weather.read",
    description="Get the current weather for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)
def get_weather(city: str) -> str:
    # Pretend this leaks an internal contact email — the output guard scrubs it.
    return f"sunny, 22C in {city} (ops: oncall@corp.com)"


def main() -> None:
    # 1. Export tool schemas in each provider's native shape.
    for dialect in ("anthropic", "openai", "gemini"):
        print(f"\n== {dialect} tool schema ==")
        print(registry.schemas(dialect))

    # 2. The same guarded dispatch handles each provider's tool-call format.
    #    Anthropic gives a dict input; OpenAI a JSON string; Gemini a dict args.
    calls = {
        "anthropic": ("tu_1", "get_weather", {"city": "Paris"}),
        "openai": ("call_1", "get_weather", '{"city": "Tokyo"}'),
        "gemini": ("n/a", "get_weather", {"city": "Lima"}),
    }

    audit = ListSink()
    print("\n== dispatch under one safety policy ==")
    with safety_context(
        PermissionSet.of("weather.read"),
        output_guards=[RedactPII()],     # scrub the leaked email for every provider
        quota=Quota(max_calls=10),
        audit=[audit],
    ):
        for dialect, (call_id, name, args) in calls.items():
            msg = registry.safe_dispatch(dialect, call_id, name, args)
            print(f"{dialect:>10}: {msg}")

    print(f"\n{len(audit.events)} audit events recorded; "
          f"e.g. {audit.events[0].to_dict()}")


if __name__ == "__main__":
    main()
