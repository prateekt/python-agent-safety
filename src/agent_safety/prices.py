"""Built-in model price table — a convenience so you can name a model instead of
typing a :class:`~agent_safety.usage.Price`.

    ask = metered(client.messages.create, model="claude-opus-4-8")
    with safely(allow="...", budget="$100"):
        ...                       # cost auto-computed from the table, capped at $100

**Read this:** these are *approximate published list prices in USD per 1,000,000
tokens, as a convenience snapshot — they go stale.** Always verify against the
provider's current pricing for anything that matters, and override with an explicit
``price=Price(input=..., output=...)`` — which always wins.

If a model isn't in the table, :func:`price_for` **raises** rather than guessing —
a silent ``$0`` would make a money budget do nothing, which is worse than an error.
Matching is by substring, so versioned names (``claude-opus-4-8-20260514``) resolve.
"""

from __future__ import annotations

from typing import List, Tuple

from .usage import Price

# (substring, Price). More specific entries first — substring matching is greedy,
# so "gpt-4o-mini" must precede "gpt-4o". USD per 1,000,000 tokens (input, output).
_PRICES: List[Tuple[str, Price]] = [
    # Anthropic
    ("claude-opus", Price(15.0, 75.0)),
    ("claude-sonnet", Price(3.0, 15.0)),
    ("claude-haiku", Price(0.80, 4.0)),
    ("opus", Price(15.0, 75.0)),
    ("sonnet", Price(3.0, 15.0)),
    ("haiku", Price(0.80, 4.0)),
    # OpenAI
    ("gpt-4o-mini", Price(0.15, 0.60)),
    ("gpt-4o", Price(2.50, 10.0)),
    ("gpt-4.1-mini", Price(0.40, 1.60)),
    ("gpt-4.1", Price(2.0, 8.0)),
    # Google Gemini
    ("gemini-2.5-pro", Price(1.25, 10.0)),
    ("gemini-2.0-flash", Price(0.10, 0.40)),
    ("gemini-1.5-flash", Price(0.075, 0.30)),
    ("gemini-1.5-pro", Price(1.25, 5.0)),
    ("gemini-flash", Price(0.10, 0.40)),
    ("gemini-pro", Price(1.25, 5.0)),
]


def price_for(model: str) -> Price:
    """Look up the built-in :class:`Price` for *model* (substring match).

    Raises :class:`ValueError` if the model isn't known — pass an explicit
    ``price=Price(...)`` instead. Never returns a zero/guessed price.
    """
    key = model.lower()
    for substring, price in _PRICES:
        if substring in key:
            return price
    known = ", ".join(sorted({s for s, _ in _PRICES}))
    raise ValueError(
        f"no built-in price for model {model!r}; pass price=Price(input=..., output=...) "
        f"explicitly. Known prefixes: {known}"
    )
