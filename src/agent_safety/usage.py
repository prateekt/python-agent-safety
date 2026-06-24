"""Automatic token accounting — so you barely have to report anything.

`agent_safety` never makes the model call itself (zero dependencies, no SDK), so
it can't *see* token usage automatically. But it *can* understand the handful of
shapes providers report usage in. That turns "dig the right attribute out of each
provider's response and call ``charge_tokens``" into one of:

* :func:`charge_usage` — hand it the model response; it finds the token count and
  charges it. One line, provider-agnostic.
* :func:`metered` — wrap your model-call function once; every call then charges
  itself (the call, its tokens, and — with a price — the dollar cost) with
  **zero** per-call reporting.

      ask = metered(client.messages.create,         # Anthropic, or any callable
                    price=Price(input=3.0, output=15.0))   # $ / 1M tokens
      with safely(allow="...", calls=100, tokens=200_000, usd=5.00):
          resp = ask(model="...", messages=[...])   # call + tokens + cost auto-charged,
                                                    # and it stops at $5.00 of spend

Recognized usage shapes (object attributes or dict keys, no SDK import):

* **Gemini**:    ``usage_metadata.total_token_count``
* **OpenAI**:    ``usage.total_tokens`` (or ``prompt_tokens`` + ``completion_tokens``)
* **Anthropic**: ``usage.input_tokens`` + ``usage.output_tokens``

If none match, :func:`extract_tokens` returns ``None`` and nothing is charged —
fall back to ``charge_tokens(n)`` with your own number.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .context import charge_call, charge_cost, charge_tokens


@dataclass(frozen=True)
class TokenUsage:
    """Tokens a model reported for one call: input, output, and total."""

    input: int = 0
    output: int = 0
    total: int = 0


@dataclass(frozen=True)
class Price:
    """Model pricing, in **US dollars per 1,000,000 tokens**.

    e.g. ``Price(input=3.0, output=15.0)`` is $3 / Mtok in, $15 / Mtok out.
    Combine with a :class:`~agent_safety.quota.CostBudget` (``safely(usd=...)``)
    to cap spend.
    """

    input: float = 0.0
    output: float = 0.0

    def cost(self, usage: TokenUsage) -> float:
        """Dollar cost of *usage* at this price."""
        return usage.input / 1_000_000 * self.input + usage.output / 1_000_000 * self.output


def _get(obj: Any, name: str) -> Any:
    """Read *name* from a mapping or an object attribute — whichever it is."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _first_int(obj: Any, *names: str) -> Optional[int]:
    """First field among *names* that holds a plain int."""
    for name in names:
        value = _get(obj, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def extract_usage(response: Any) -> Optional[TokenUsage]:
    """Best-effort :class:`TokenUsage` from a provider response, or ``None``.

    Reads the usage object (``usage_metadata`` or ``usage``, else the response
    itself) and the common per-provider field names — Gemini
    (``prompt_token_count`` / ``candidates_token_count`` / ``total_token_count``),
    OpenAI (``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``), and
    Anthropic (``input_tokens`` / ``output_tokens``). No provider SDK is imported.
    """
    usage = _get(response, "usage_metadata")
    if usage is None:
        usage = _get(response, "usage")
    if usage is None:
        usage = response  # maybe the usage object was passed directly

    inp = _first_int(usage, "prompt_token_count", "prompt_tokens", "input_tokens")
    out = _first_int(usage, "candidates_token_count", "completion_tokens", "output_tokens")
    total = _first_int(usage, "total_token_count", "total_tokens")
    if inp is None and out is None and total is None:
        return None
    inp, out = inp or 0, out or 0
    return TokenUsage(inp, out, total if total is not None else inp + out)


def extract_tokens(response: Any) -> Optional[int]:
    """Best-effort total token count from a provider response, or ``None``."""
    usage = extract_usage(response)
    return usage.total if usage is not None else None


def charge_usage(response: Any, price: Optional[Price] = None) -> int:
    """Charge the tokens (and, given a *price*, the cost) reported in *response*.

    Returns the number of tokens charged (``0`` if none were found). Raises
    :class:`~agent_safety.exceptions.QuotaExceeded` /
    :class:`~agent_safety.exceptions.CostBudgetExceeded` if it pushes a token or
    money budget over its limit.
    """
    usage = extract_usage(response)
    if usage is None:
        return 0
    if usage.total:
        charge_tokens(usage.total)
    if price is not None:
        charge_cost(price.cost(usage))
    return usage.total


def metered(fn: Callable[..., Any], price: Optional[Price] = None) -> Callable[..., Any]:
    """Wrap a model-call function so each call auto-charges itself.

    On every call: one call is charged against the active quota / rate limit /
    deadline *before* the request, and the response's tokens (and, with a *price*,
    the dollar cost) are charged *after*. Works on sync and async callables
    (auto-detected), so you wrap your model client's method once and stop reporting
    usage by hand.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            charge_call()
            result = await fn(*args, **kwargs)
            charge_usage(result, price)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        charge_call()
        result = fn(*args, **kwargs)
        charge_usage(result, price)
        return result

    return wrapper
