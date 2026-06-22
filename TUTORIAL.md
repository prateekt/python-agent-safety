# Your first safe agent

A 10-minute, copy-paste tutorial. By the end you'll have a working agent that can
use tools — but can only do what you let it, on a budget, with secrets scrubbed.

You need: Python 3.9+ and this library.

```bash
pip install -e .
```

Everything here runs offline. No API keys, no accounts.

---

## The big idea

An "agent" is a program that lets a model *use tools* — read a file, call an API,
run a command. That's powerful, and a little scary: what if it reads the wrong
file, or runs forever, or leaks a password?

`agent_safety` gives you two words to make that safe:

- **`@tool`** — mark a function the agent is allowed to call.
- **`safely(...)`** — a block that says, in plain words, what's allowed *right now*.

That's the whole tutorial. Let's build it up.

---

## Step 1 — make a tool

A tool is just a normal function with a `@tool` sticker on it.

```python
from agent_safety import tool

@tool
def get_weather(city):
    return f"It is sunny in {city}."
```

If you call it right now, it won't run:

```python
get_weather("Paris")
# PermissionDenied: capability 'get_weather' denied
```

That's on purpose. A tool only works **inside** a `safely(...)` block that allows
it — so nothing happens by accident.

## Step 2 — allow it

```python
from agent_safety import tool, safely

@tool
def get_weather(city):
    return f"It is sunny in {city}."

with safely(allow="get_weather"):
    print(get_weather("Paris"))     # It is sunny in Paris.
```

The capability is named after the function, so `allow="get_weather"` matches. You
can allow several: `allow=["get_weather", "read_notes"]`, or everything with
`allow="everything"`.

## Step 3 — it blocks what you didn't allow

Add a second, more dangerous tool:

```python
@tool
def delete_everything():
    return "all gone"

with safely(allow="get_weather"):       # we did NOT allow delete_everything
    get_weather("Paris")                # fine
    delete_everything()                 # PermissionDenied 🎉
```

This is the core safety idea — **least privilege**. Even if the model is tricked
into trying something, it can only do what you listed.

## Step 4 — add some rules

`safely(...)` takes plain keywords. Use the ones you need; ignore the rest.

```python
with safely(
    allow="get_weather",
    calls=10,             # at most 10 tool calls
    seconds=30,           # at most 30 seconds of work
    hide_secrets=True,    # scrub emails / API keys out of results
    explain=True,         # the agent must say WHY before each call
    log=True,             # print every decision so you can watch
):
    get_weather("Paris", rationale="The user asked for the weather")
```

With `explain=True`, a call without a `rationale="..."` is refused — a built-in
record of *why* the agent did what it did.

---

## Step 5 — the agent loop

Real agents run a loop: the model asks to use a tool, you run it, you hand back
the result, repeat — until the model gives a final answer. Here's that loop, with
a tiny stand-in "model" so it runs offline. (The full file is
[`examples/first_agent.py`](examples/first_agent.py) — run it!)

```python
from agent_safety import ToolRegistry, parse_tool_calls, safely

registry = ToolRegistry()

@registry.tool("weather.lookup", description="Get the weather for a city.")
def get_weather(city):
    return f"It is sunny in {city}."

@registry.tool("notes.read", description="Read the user's note on a topic.")
def read_notes(topic):
    return f"Your {topic} note says: ping jane@private.com about the budget."

# ... a TinyModel that decides which tool to call (see the example file) ...

with safely(allow=["weather.lookup", "notes.read"], calls=5, hide_secrets=True):
    for _ in range(5):
        response = model.respond(question, results)
        calls = parse_tool_calls("anthropic", response)   # read the model's request
        if not calls:
            print("Agent:", response["content"][0]["text"])
            break
        for call in calls:
            result = registry.safe_dispatch(                # run it — safely
                "anthropic", call.id, call.name, call.arguments)
            results.append(result["content"])
```

Running it:

```
User: What does my budget note say?
  → calling read_notes({'topic': 'budget'})
Agent: Here's what I found: Your budget note says: ping me at [REDACTED:EMAIL] about the budget.
```

Notice the email was **scrubbed automatically** on the way back — the model never
saw the secret, because the whole loop ran inside `safely(..., hide_secrets=True)`.

## Step 6 — use a real model

The loop above never imported an AI SDK. To use a real one, you change *how the
response is produced* — the safety parts don't move:

```python
import anthropic
client = anthropic.Anthropic()             # reads ANTHROPIC_API_KEY

with safely(allow=["weather.lookup", "notes.read"], calls=5, hide_secrets=True):
    response = client.messages.create(
        model="claude-opus-4-8",
        tools=registry.schemas("anthropic"),   # tool schemas, auto-generated
        messages=messages,
    )
    for call in parse_tool_calls("anthropic", response):       # same line as before
        result = registry.safe_dispatch("anthropic", call.id, call.name, call.arguments)
        messages.append({"role": "user", "content": [result]})
```

The same code works for OpenAI and Gemini — just change `"anthropic"` to
`"openai"` or `"gemini"`. See [`examples/providers.py`](examples/providers.py).

---

## `safely(...)` cheat sheet

| Keyword | What it does |
|---|---|
| `allow=` | what the agent may do — a name, a list, or `"everything"` |
| `deny=` | things to forbid even if allowed (deny always wins) |
| `calls=` | most tool calls |
| `tokens=` | most model tokens (you report them) |
| `per_second=` / `per_minute=` | speed limit |
| `seconds=` | time budget |
| `hide_secrets=True` | scrub emails / API keys / tokens from results |
| `max_input=` | reject inputs longer than N characters |
| `block=` | reject text matching a pattern (e.g. `"rm -rf"`) |
| `block_injections=True` | reject "ignore previous instructions" inputs |
| `clean_text=True` | strip hidden/invisible characters |
| `no_repeats=` | stop after N identical calls (runaway loop) |
| `ask=` | ask before acting: `True` (console y/n) or your own function |
| `explain=` | require a `rationale="..."` with each call |
| `log=` | watch what happens: `True` (print) or your own recorder |

All optional. Start with `allow=` and add more as you need them.

---

## Where to go next

- Run the examples: [`examples/easy.py`](examples/easy.py) and
  [`examples/first_agent.py`](examples/first_agent.py).
- Outgrowing the keywords? Every one maps to a real object you can use directly —
  see "The full version" in the [README](README.md).
- The honest part: these are strong defaults, not a magic shield. The durable
  guarantee is least privilege — the agent simply cannot do what you didn't
  `allow`. Layer real sandboxing/moderation behind it for production.

That's it. You built a safe agent. 🎉
