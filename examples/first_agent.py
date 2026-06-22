"""Your first safe agent — a complete, runnable tool-calling loop.

    python examples/first_agent.py

There are no API keys here: a tiny stand-in "model" decides which tool to call so
the whole thing runs offline. But it goes through the *exact* same path a real
Claude / OpenAI / Gemini agent would — `parse_tool_calls` reads the model's
response, `registry.safe_dispatch` runs each call through the safety policy — so
swapping in a real model later changes ~5 lines (see the bottom of this file).

The point: the whole loop runs inside one `safely(...)` block, so the agent can
only touch what you allowed, on a budget, with secrets scrubbed — no matter what
the model decides to do.
"""

from agent_safety import ToolRegistry, parse_tool_calls, safely

registry = ToolRegistry()


@registry.tool("weather.lookup", description="Get the weather for a city.")
def get_weather(city: str) -> str:
    return f"It is sunny and 21°C in {city}."


@registry.tool("notes.read", description="Read the user's note on a topic.")
def read_notes(topic: str) -> str:
    # This note happens to contain a secret — watch it get scrubbed on the way out.
    return f"Your {topic} note says: ping me at jane@private.com about the budget."


class TinyModel:
    """A pretend model. It looks at the conversation and decides what to do next.

    A real model would do this with actual reasoning; we hard-code it so the
    example is deterministic and needs no network. It speaks the Anthropic
    response shape (a list of content blocks).
    """

    def respond(self, question: str, tool_results: list) -> dict:
        # Once a tool has answered, give a final text reply.
        if tool_results:
            facts = " ".join(tool_results)
            return {"content": [{"type": "text", "text": f"Here's what I found: {facts}"}]}
        # Otherwise, pick a tool based on the question.
        if "weather" in question.lower():
            return {"content": [{"type": "tool_use", "id": "t1",
                                 "name": "get_weather", "input": {"city": "Paris"}}]}
        if "note" in question.lower():
            return {"content": [{"type": "tool_use", "id": "t1",
                                 "name": "read_notes", "input": {"topic": "budget"}}]}
        return {"content": [{"type": "text", "text": "I'm not sure how to help with that."}]}


def run_agent(question: str) -> None:
    print(f"\nUser: {question}")
    model = TinyModel()
    tool_results: list = []

    # Everything the agent does happens inside this one block.
    with safely(
        allow=["weather.lookup", "notes.read"],  # the only two things it may do
        calls=5,                                  # a small budget
        hide_secrets=True,                        # scrub secrets from tool results
    ):
        for _ in range(5):  # a few turns at most
            response = model.respond(question, tool_results)
            calls = parse_tool_calls("anthropic", response)

            if not calls:  # the model gave a final answer
                print("Agent:", response["content"][0]["text"])
                return

            for call in calls:
                print(f"  → calling {call.name}({call.arguments})")
                result = registry.safe_dispatch("anthropic", call.id, call.name, call.arguments)
                tool_results.append(result["content"])  # already secret-scrubbed


def main() -> None:
    run_agent("What's the weather?")
    run_agent("What does my budget note say?")  # note the email is redacted in the reply


if __name__ == "__main__":
    main()


# --- Connecting a real model -------------------------------------------------
# The loop above never imported an SDK. To use a real model, you only change how
# `response` is produced and where the results go back — the safety parts are
# identical:
#
#     import anthropic
#     client = anthropic.Anthropic()              # reads ANTHROPIC_API_KEY
#     with safely(allow=["weather.lookup", "notes.read"], calls=5, hide_secrets=True):
#         response = client.messages.create(
#             model="claude-opus-4-8",
#             tools=registry.schemas("anthropic"),  # <- tool schemas, auto-generated
#             messages=messages,
#         )
#         for call in parse_tool_calls("anthropic", response):     # <- same call
#             result = registry.safe_dispatch("anthropic", call.id, call.name, call.arguments)
#             messages.append({"role": "user", "content": [result]})
#
# Same for OpenAI / Gemini — just change the dialect string. See examples/providers.py.
