"""Automatic token accounting — so you barely have to report anything.

`agent_safety` never makes the model call itself (zero dependencies, no SDK), so
it can't *see* token usage automatically. But it *can* understand the handful of
shapes providers report usage in. That turns "dig the right attribute out of each
provider's response and call ``charge_tokens``" into one of:

* :func:`charge_usage` — hand it the model response; it finds the token count and
  charges it. One line, provider-agnostic.
* :func:`metered` — wrap your model-call function once; every call then charges
  itself (the call *and* its tokens) with **zero** per-call reporting.

      ask = metered(client.messages.create)        # Anthropic, or any callable
      with safely(allow="...", calls=100, tokens=200_000):
          resp = ask(model="...", messages=[...])   # call + tokens auto-charged

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
from typing import Any, Callable, Mapping, Optional

from .context import charge_call, charge_tokens


def _get(obj: Any, name: str) -> Any:
    """Read *name* from a mapping or an object attribute — whichever it is."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def extract_tokens(response: Any) -> Optional[int]:
    """Best-effort total token count from a provider response, or ``None``.

    Looks for the usage object (``usage_metadata`` or ``usage``, else the response
    itself) and the common count fields. No provider SDK is imported.
    """
    usage = _get(response, "usage_metadata")
    if usage is None:
        usage = _get(response, "usage")
    if usage is None:
        usage = response  # maybe the usage object was passed directly

    for total_key in ("total_token_count", "total_tokens"):
        total = _as_int(_get(usage, total_key))
        if total is not None:
            return total

    for first, second in (("input_tokens", "output_tokens"),
                          ("prompt_tokens", "completion_tokens")):
        a, b = _as_int(_get(usage, first)), _as_int(_get(usage, second))
        if a is not None or b is not None:
            return (a or 0) + (b or 0)

    return None


def charge_usage(response: Any) -> int:
    """Charge the tokens reported in *response* to the active policy.

    Returns the number of tokens charged (``0`` if none were found). Raises
    :class:`~agent_safety.exceptions.QuotaExceeded` if it pushes a token budget
    over its limit, exactly like a manual ``charge_tokens`` call.
    """
    tokens = extract_tokens(response)
    if tokens:
        charge_tokens(tokens)
        return tokens
    return 0


def metered(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a model-call function so each call auto-charges itself.

    On every call: one call is charged against the active quota / rate limit /
    deadline *before* the request, and the response's tokens are charged *after*.
    Works on sync and async callables (auto-detected), so you wrap your model
    client's method once and stop reporting usage by hand.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            charge_call()
            result = await fn(*args, **kwargs)
            charge_usage(result)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        charge_call()
        result = fn(*args, **kwargs)
        charge_usage(result)
        return result

    return wrapper
