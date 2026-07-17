"""The serializable form of a ``safely(...)`` policy.

A :class:`PolicySpec` is the JSON-serializable counterpart of the keywords you
would pass to :func:`~agent_safety.easy.safely` — the single config schema for
shipping a policy between processes, publishing it to a registry, or minting a
signed envelope from it. Every declarative ``safely`` keyword has a field here;
only the *callable* hooks (an ``ask=`` approver, a ``judge=``, a ``preview=``
approver, a ``log=`` sink) cannot be serialized — supply those at runtime via
:func:`safely_from_spec`.

Round trip: ``safely(**spec.to_kwargs())`` enforces exactly what the spec says,
and ``PolicySpec.from_safely_kwargs(...)`` captures the same keywords back into
a spec. A machine-readable JSON Schema ships at ``schemas/policy_spec.schema.json``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, ContextManager, Dict, Iterable, Optional, Tuple, Union

from ..core.permissions import PermissionSet
from ..core.policy import Policy
from .backends import BudgetBackend, BudgetLimits


def _canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _names(value: Union[str, Iterable[str], None]) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class PolicySpec:
    """JSON-serializable policy configuration — every declarative ``safely`` keyword."""

    version: int = 1
    # what the agent may do
    allow: Tuple[str, ...] = ()
    deny: Tuple[str, ...] = ()
    # budgets
    calls: Optional[int] = None
    tokens: Optional[int] = None
    per_second: Optional[int] = None
    per_minute: Optional[int] = None
    seconds: Optional[float] = None          # total wall-clock budget (safely total_seconds=)
    at_most: Optional[int] = None
    no_repeats: Optional[int] = None
    risk_budget: Optional[int] = None
    budget: Optional[float] = None           # dollars
    timeout: Optional[float] = None          # per-call seconds
    memory: Optional[int] = None             # bytes of Python-heap growth
    # input hygiene
    max_input: Optional[int] = None
    block: Tuple[str, ...] = ()
    block_injections: bool = False
    clean_text: bool = False
    honeytoken: Optional[str] = None
    # output hygiene
    hide_secrets: bool = False
    # gates (declarative parts; callable hooks are supplied at runtime)
    ask: bool = False
    explain: Tuple[str, ...] = ()            # capability patterns; ("*",) = all
    rules: Tuple[str, ...] = ()              # plain-English constitution rules
    # mode
    monitor: bool = False
    log: bool = False

    _LIST_FIELDS = ("allow", "deny", "block", "explain", "rules")
    _SCALAR_FIELDS = (
        "calls", "tokens", "per_second", "per_minute", "seconds", "at_most",
        "no_repeats", "risk_budget", "budget", "timeout", "memory",
        "max_input", "honeytoken",
    )
    _FLAG_FIELDS = ("block_injections", "clean_text", "hide_secrets", "ask", "monitor", "log")

    def to_dict(self) -> Dict[str, Any]:
        """A compact dict: only non-default fields are included (stable hashing)."""
        d: Dict[str, Any] = {"version": self.version}
        for key in self._LIST_FIELDS:
            val = getattr(self, key)
            if val:
                d[key] = list(val)
        for key in self._SCALAR_FIELDS:
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        for key in self._FLAG_FIELDS:
            if getattr(self, key):
                d[key] = True
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicySpec":
        kwargs: Dict[str, Any] = {"version": int(data.get("version", 1))}
        for key in cls._LIST_FIELDS:
            kwargs[key] = tuple(data.get(key, ()))
        for key in cls._SCALAR_FIELDS:
            kwargs[key] = data.get(key)
        for key in cls._FLAG_FIELDS:
            kwargs[key] = bool(data.get(key, False))
        return cls(**kwargs)

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def policy_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def permissions(self) -> PermissionSet:
        allow = list(self.allow) or ["*"]
        return PermissionSet.of(*allow, deny=self.deny)

    def budget_limits(self) -> BudgetLimits:
        rate = self.per_second
        window = 1.0
        if self.per_minute is not None:
            rate = self.per_minute
            window = 60.0
        return BudgetLimits(
            max_calls=self.calls,
            max_tokens=self.tokens,
            rate_per_second=rate,
            rate_window=window,
            max_identical=self.no_repeats,
            max_concurrent=self.at_most,
            max_risk=self.risk_budget,
        )

    def to_kwargs(self) -> Dict[str, Any]:
        """The equivalent :func:`~agent_safety.easy.safely` keyword arguments.

        ``safely(**spec.to_kwargs())`` enforces exactly this spec. Callable
        hooks (a custom approver, judge, preview approver, or log sink) are not
        part of a serializable spec — pass them to :func:`safely_from_spec`.
        """
        kwargs: Dict[str, Any] = {}
        if self.allow:
            kwargs["allow"] = list(self.allow)
        if self.deny:
            kwargs["deny"] = list(self.deny)
        if self.seconds is not None:
            kwargs["total_seconds"] = self.seconds
        for key in (
            "calls", "tokens", "per_second", "per_minute", "at_most",
            "no_repeats", "risk_budget", "budget", "timeout", "memory",
            "max_input", "honeytoken",
        ):
            val = getattr(self, key)
            if val is not None:
                kwargs[key] = val
        if self.block:
            kwargs["block"] = list(self.block)
        for key in ("block_injections", "clean_text", "hide_secrets", "monitor"):
            if getattr(self, key):
                kwargs[key] = True
        if self.ask:
            kwargs["ask"] = True
        if self.explain:
            kwargs["explain"] = True if self.explain == ("*",) else list(self.explain)
        if self.log:
            kwargs["log"] = True
        # rules need a judge= callable; safely_from_spec pairs them at runtime.
        return kwargs

    def narrow(self, other: "PolicySpec") -> "PolicySpec":
        """Return a stricter spec — the same one-way ratchet as nested ``safely``.

        Permissions are intersected, numeric limits take the tighter value,
        pattern lists and flags are unioned. ``monitor`` follows the runtime
        ratchet: the result monitors only if *both* specs monitor — if either
        side enforces, the narrowed spec enforces.
        """
        perms = self.permissions().intersect(other.permissions())
        allow = tuple(sorted(perms.allow))
        deny = tuple(sorted(perms.deny))

        def _tighter(a: Optional[Any], b: Optional[Any]) -> Optional[Any]:
            vals = [v for v in (a, b) if v is not None]
            return min(vals) if vals else None

        def _union(a: Tuple[str, ...], b: Tuple[str, ...]) -> Tuple[str, ...]:
            return tuple(sorted(set(a) | set(b)))

        return replace(
            self,
            allow=allow,
            deny=deny,
            calls=_tighter(self.calls, other.calls),
            tokens=_tighter(self.tokens, other.tokens),
            per_second=_tighter(self.per_second, other.per_second),
            per_minute=_tighter(self.per_minute, other.per_minute),
            seconds=_tighter(self.seconds, other.seconds),
            at_most=_tighter(self.at_most, other.at_most),
            no_repeats=_tighter(self.no_repeats, other.no_repeats),
            risk_budget=_tighter(self.risk_budget, other.risk_budget),
            budget=_tighter(self.budget, other.budget),
            timeout=_tighter(self.timeout, other.timeout),
            memory=_tighter(self.memory, other.memory),
            max_input=_tighter(self.max_input, other.max_input),
            block=_union(self.block, other.block),
            block_injections=self.block_injections or other.block_injections,
            clean_text=self.clean_text or other.clean_text,
            honeytoken=self.honeytoken or other.honeytoken,
            hide_secrets=self.hide_secrets or other.hide_secrets,
            ask=self.ask or other.ask,
            explain=_union(self.explain, other.explain),
            rules=_union(self.rules, other.rules),
            # Runtime ratchet: monitor can only tighten to enforce, never the
            # other way — so a narrowed spec monitors only if both sides do.
            monitor=self.monitor and other.monitor,
            log=self.log or other.log,
        )

    @classmethod
    def from_safely_kwargs(
        cls,
        allow: Any = None,
        deny: Any = None,
        **kwargs: Any,
    ) -> "PolicySpec":
        """Capture the same keywords as :func:`~agent_safety.easy.safely` into a spec.

        Callable values (a custom ``ask=`` approver, ``judge=``, ``preview=``,
        or ``log=`` sink) are recorded as their boolean presence — supply the
        callables again at runtime via :func:`safely_from_spec`.
        """
        budget = kwargs.get("budget")
        if isinstance(budget, str):
            budget = float(budget.strip().lstrip("$").replace(",", ""))
        memory = kwargs.get("memory")
        if isinstance(memory, str):
            from ..easy import _bytes
            memory = _bytes(memory)
        explain = kwargs.get("explain")
        if explain is True:
            explain_patterns: Tuple[str, ...] = ("*",)
        else:
            explain_patterns = _names(explain)
        return cls(
            allow=_names(allow),
            deny=_names(deny),
            calls=kwargs.get("calls"),
            tokens=kwargs.get("tokens"),
            per_second=kwargs.get("per_second"),
            per_minute=kwargs.get("per_minute"),
            seconds=kwargs.get("total_seconds", kwargs.get("seconds")),
            at_most=kwargs.get("at_most"),
            no_repeats=kwargs.get("no_repeats"),
            risk_budget=kwargs.get("risk_budget"),
            budget=budget,
            timeout=kwargs.get("timeout"),
            memory=memory,
            max_input=kwargs.get("max_input"),
            block=_names(kwargs.get("block")),
            block_injections=bool(kwargs.get("block_injections", False)),
            clean_text=bool(kwargs.get("clean_text", False)),
            honeytoken=kwargs.get("honeytoken"),
            hide_secrets=bool(kwargs.get("hide_secrets", False)),
            ask=bool(kwargs.get("ask", False)),
            explain=explain_patterns,
            rules=_names(kwargs.get("rule")),
            monitor=bool(kwargs.get("monitor", False)),
            log=bool(kwargs.get("log", False)),
        )


@dataclass(frozen=True)
class PolicyRegistryEntry:
    spec: PolicySpec
    version: int
    published_at: float
    published_by: str = "system"


class PolicyRegistry:
    """Versioned :class:`PolicySpec` store with hot-reload."""

    def __init__(self) -> None:
        self._entries: Dict[str, PolicyRegistryEntry] = {}
        self._by_hash: Dict[str, str] = {}
        self._lock = Lock()

    def publish(
        self,
        spec: PolicySpec,
        *,
        version: Optional[int] = None,
        published_by: str = "system",
    ) -> str:
        """Register *spec* and return its ``policy_hash``."""
        phash = spec.policy_hash()
        ver = version if version is not None else int(time.time())
        entry = PolicyRegistryEntry(
            spec=spec,
            version=ver,
            published_at=time.time(),
            published_by=published_by,
        )
        with self._lock:
            self._entries[phash] = entry
            self._by_hash[phash] = phash
        return phash

    def resolve(self, policy_hash: str) -> Optional[PolicySpec]:
        with self._lock:
            entry = self._entries.get(policy_hash)
            return entry.spec if entry else None

    def rollback(self, policy_hash: str) -> bool:
        """Pin resolves to an older hash (no-op if already present)."""
        return self.resolve(policy_hash) is not None


def safely_from_spec(
    spec: PolicySpec,
    *,
    ask: Any = None,
    judge: Any = None,
    preview: Any = None,
    log: Any = None,
    backend: Optional[BudgetBackend] = None,
    envelope: Any = None,
    run: Any = None,
) -> ContextManager[Policy]:
    """A ``safely(...)`` context built from *spec*, plus any runtime hooks.

    The spec carries everything declarative; the keyword arguments here supply
    the callables a spec cannot serialize:

    * ``ask=`` — an approver function (overrides the spec's console default);
    * ``judge=`` — required when the spec carries ``rules``;
    * ``preview=`` — a preview approver;
    * ``log=`` — an audit sink (overrides the spec's print default).
    """
    from ..easy import safely

    kwargs = spec.to_kwargs()
    if ask is not None:
        kwargs["ask"] = ask
    if spec.rules:
        if judge is None:
            raise TypeError(
                "this PolicySpec carries rules; pass judge= to safely_from_spec"
            )
        kwargs["rule"] = list(spec.rules)
        kwargs["judge"] = judge
    if preview is not None:
        kwargs["preview"] = preview
    if log is not None:
        kwargs["log"] = log
    if envelope is not None:
        kwargs["envelope"] = envelope
    if run is not None:
        kwargs["run"] = run
    if backend is not None:
        kwargs["backend"] = backend
    return safely(**kwargs)
