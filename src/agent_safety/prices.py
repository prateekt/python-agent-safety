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


def _round(value: float) -> float:
    return round(value, 4)


def _anthropic(inp: float, out: float) -> Price:
    """Anthropic prompt caching: reads ~0.1x input, writes ~1.25x input."""
    return Price(inp, out, cached=_round(inp * 0.10), cache_write=_round(inp * 1.25))


def _openai(inp: float, out: float) -> Price:
    """OpenAI prompt caching: cached input billed at ~0.5x (no write surcharge)."""
    return Price(inp, out, cached=_round(inp * 0.50))


def _gemini(inp: float, out: float) -> Price:
    """Gemini context caching: cached input billed at ~0.25x."""
    return Price(inp, out, cached=_round(inp * 0.25))


# (substring, Price). More specific entries first — substring matching is greedy,
# so "gpt-4o-mini" must precede "gpt-4o". USD per 1,000,000 tokens (input, output);
# cache read/write rates are set per provider above.
_PRICES: List[Tuple[str, Price]] = [
    # Anthropic
    ("claude-opus", _anthropic(15.0, 75.0)),
    ("claude-sonnet", _anthropic(3.0, 15.0)),
    ("claude-haiku", _anthropic(0.80, 4.0)),
    ("opus", _anthropic(15.0, 75.0)),
    ("sonnet", _anthropic(3.0, 15.0)),
    ("haiku", _anthropic(0.80, 4.0)),
    # OpenAI
    ("gpt-4o-mini", _openai(0.15, 0.60)),
    ("gpt-4o", _openai(2.50, 10.0)),
    ("gpt-4.1-mini", _openai(0.40, 1.60)),
    ("gpt-4.1", _openai(2.0, 8.0)),
    # Google Gemini
    ("gemini-2.5-pro", _gemini(1.25, 10.0)),
    ("gemini-2.0-flash", _gemini(0.10, 0.40)),
    ("gemini-1.5-flash", _gemini(0.075, 0.30)),
    ("gemini-1.5-pro", _gemini(1.25, 5.0)),
    ("gemini-flash", _gemini(0.10, 0.40)),
    ("gemini-pro", _gemini(1.25, 5.0)),
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
