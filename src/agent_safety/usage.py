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
                    model="claude-opus-4-8")        # price from the built-in table
      with safely(allow="...", budget="$100"):       # "spend at most $100"
          resp = ask(model="...", messages=[...])   # call + tokens + cost auto-charged,
                                                    # and it stops at $100 of spend

Recognized usage shapes (object attributes or dict keys, no SDK import) — Gemini,
OpenAI, and Anthropic, including their **cache-read / cache-write** token fields,
which are priced separately. **Streaming** works too: :func:`metered` detects a
sync or async stream of chunks and charges usage once it's consumed.

If nothing matches, :func:`extract_tokens` returns ``None`` and nothing is charged —
fall back to ``charge_tokens(n)`` with your own number.

(Reasoning/"thinking" tokens are billed at the output rate; where a provider folds
them into the output count — e.g. OpenAI ``reasoning_tokens`` — they're already
captured.)
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Optional

from .context import charge_call, charge_cost, charge_tokens


@dataclass(frozen=True)
class TokenUsage:
    """Tokens a model reported for one call, in **non-overlapping** buckets.

    ``input`` is full-price prompt tokens; ``cached`` is cache-*read* tokens (a
    discount); ``cache_write`` is cache-*creation* tokens (a surcharge on some
    providers); ``output`` is completion tokens. ``total`` is every token, used for
    a token budget. Each token is counted in exactly one bucket, so cost is just the
    weighted sum.
    """

    input: int = 0
    output: int = 0
    cached: int = 0
    cache_write: int = 0
    total: int = 0


@dataclass(frozen=True)
class Price:
    """Model pricing, in **US dollars per 1,000,000 tokens**.

    e.g. ``Price(input=3.0, output=15.0)`` is $3 / Mtok in, $15 / Mtok out. Cache
    reads and writes are priced separately — ``cached`` / ``cache_write`` — and
    default to the full ``input`` rate when unset (conservative: never under-charges).
    The built-in table (``model="..."``) sets them to each provider's real discount.
    Combine with a :class:`~agent_safety.quota.CostBudget` (``safely(budget="$100")``)
    to cap spend.
    """

    input: float = 0.0
    output: float = 0.0
    cached: Optional[float] = None
    cache_write: Optional[float] = None

    def cost(self, usage: TokenUsage) -> float:
        """Dollar cost of *usage* at this price (per-bucket weighted sum)."""
        cached_rate = self.input if self.cached is None else self.cached
        write_rate = self.input if self.cache_write is None else self.cache_write
        return (
            usage.input * self.input
            + usage.cached * cached_rate
            + usage.cache_write * write_rate
            + usage.output * self.output
        ) / 1_000_000


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
    itself) and the common per-provider field names — Gemini, OpenAI, Anthropic —
    including **cache** tokens, which providers report two different ways:

    * **Anthropic** reports ``cache_read_input_tokens`` / ``cache_creation_input_tokens``
      *separately* from ``input_tokens`` (additive).
    * **OpenAI** (``prompt_tokens_details.cached_tokens``) and **Gemini**
      (``cached_content_token_count``) report cached tokens as a *subset* already
      inside the prompt count.

    Either way the result is non-overlapping buckets, so cost is a clean weighted
    sum. No provider SDK is imported. ``total`` is the provider's total, or the sum
    of the buckets, whichever is larger (never under-counts a token budget).
    """
    usage = _get(response, "usage_metadata")
    if usage is None:
        usage = _get(response, "usage")
    if usage is None:
        usage = response  # maybe the usage object was passed directly

    inp = _first_int(usage, "prompt_token_count", "prompt_tokens", "input_tokens")
    out = _first_int(usage, "candidates_token_count", "completion_tokens", "output_tokens")
    total = _first_int(usage, "total_token_count", "total_tokens")

    # Cache-read tokens: subset-style (OpenAI/Gemini) is carved out of `inp`;
    # additive-style (Anthropic) sits alongside it.
    subset_cached = _first_int(usage, "cached_content_token_count")
    if subset_cached is None:
        details = _get(usage, "prompt_tokens_details")
        if details is not None:
            subset_cached = _first_int(details, "cached_tokens")
    additive_cached = _first_int(usage, "cache_read_input_tokens")
    cache_write = _first_int(usage, "cache_creation_input_tokens")

    if (inp is None and out is None and total is None
            and subset_cached is None and additive_cached is None and cache_write is None):
        return None

    inp, out, cw = inp or 0, out or 0, cache_write or 0
    cached = 0
    if subset_cached:
        cached += subset_cached
        inp = max(0, inp - subset_cached)  # the cached portion is not full-price input
    if additive_cached:
        cached += additive_cached          # already separate from input

    buckets = inp + out + cached + cw
    return TokenUsage(
        input=inp, output=out, cached=cached, cache_write=cw,
        total=max(total or 0, buckets),
    )


def extract_tokens(response: Any) -> Optional[int]:
    """Best-effort total token count from a provider response, or ``None``."""
    usage = extract_usage(response)
    return usage.total if usage is not None else None


def _charge(usage: TokenUsage, price: Optional[Price]) -> int:
    """Charge a resolved :class:`TokenUsage` (and its cost, given a price)."""
    if usage.total:
        charge_tokens(usage.total)
    if price is not None:
        charge_cost(price.cost(usage))
    return usage.total


def _merge(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    """Combine two usage reports from one streamed call by taking the max of each
    bucket — provider stream chunks report cumulative (monotonic) counts, and some
    (Anthropic) split input and output across different events."""
    inp, out = max(a.input, b.input), max(a.output, b.output)
    cached, cw = max(a.cached, b.cached), max(a.cache_write, b.cache_write)
    return TokenUsage(
        input=inp, output=out, cached=cached, cache_write=cw,
        total=max(a.total, b.total, inp + out + cached + cw),
    )


def charge_usage(response: Any, price: Optional[Price] = None) -> int:
    """Charge the tokens (and, given a *price*, the cost) reported in *response*.

    Returns the number of tokens charged (``0`` if none were found). Raises
    :class:`~agent_safety.exceptions.QuotaExceeded` /
    :class:`~agent_safety.exceptions.CostBudgetExceeded` if it pushes a token or
    money budget over its limit.
    """
    usage = extract_usage(response)
    return _charge(usage, price) if usage is not None else 0


def _meter_stream(stream: Any, price: Optional[Price]) -> Iterator[Any]:
    """Pass a sync stream through unchanged, charging usage once it's exhausted."""
    merged: Optional[TokenUsage] = None
    for chunk in stream:
        usage = extract_usage(chunk)
        if usage is not None:
            merged = usage if merged is None else _merge(merged, usage)
        yield chunk
    if merged is not None:
        _charge(merged, price)


async def _ameter_stream(stream: Any, price: Optional[Price]) -> AsyncIterator[Any]:
    """Async counterpart of :func:`_meter_stream`."""
    merged: Optional[TokenUsage] = None
    async for chunk in stream:
        usage = extract_usage(chunk)
        if usage is not None:
            merged = usage if merged is None else _merge(merged, usage)
        yield chunk
    if merged is not None:
        _charge(merged, price)


def _meter_result(result: Any, price: Optional[Price]) -> Any:
    """Charge a model-call result, transparently handling streamed responses."""
    if hasattr(result, "__anext__"):          # an async stream/iterator of chunks
        return _ameter_stream(result, price)
    if hasattr(result, "__next__"):           # a sync stream/iterator of chunks
        return _meter_stream(result, price)
    charge_usage(result, price)               # a plain response object
    return result


def metered(
    fn: Callable[..., Any],
    price: Optional[Price] = None,
    model: Optional[str] = None,
) -> Callable[..., Any]:
    """Wrap a model-call function so each call auto-charges itself.

    On every call: one call is charged against the active quota / rate limit /
    deadline *before* the request, and the response's tokens (and, with a price,
    the dollar cost) are charged *after*. Works on sync and async callables
    (auto-detected), so you wrap your model client's method once and stop reporting
    usage by hand.

    **Streaming** is handled transparently: if the call returns a stream (a sync or
    async iterator of chunks — e.g. OpenAI with ``stream_options={"include_usage":
    True}``, or Gemini streaming), the stream is passed through and usage is charged
    once it's fully consumed. Consume it inside the ``safely(...)`` block.

    Pricing: pass ``price=Price(input=..., output=...)`` ($ per 1M tokens), or
    ``model="claude-opus-4-8"`` to look it up in the built-in table (see
    :mod:`agent_safety.prices`). With neither, tokens are charged but cost is not.
    """
    if price is None and model is not None:
        from .prices import price_for  # lazy import to avoid a cycle (prices -> usage)

        price = price_for(model)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            charge_call()
            return _meter_result(await fn(*args, **kwargs), price)

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        charge_call()
        return _meter_result(fn(*args, **kwargs), price)

    return wrapper
