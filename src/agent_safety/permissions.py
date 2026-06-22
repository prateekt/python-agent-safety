"""Capability-based permission sets for AI agents.

A *capability* is a dotted name describing something an agent might do:
``filesystem.read``, ``filesystem.write``, ``network.http``, ``shell.exec``.

A :class:`PermissionSet` is an immutable pair of pattern collections — things
that are allowed and things that are explicitly denied. The two rules are:

1. **Deny wins.** If any deny pattern matches, the capability is forbidden even
   if an allow pattern also matches. This makes "allow ``filesystem.*`` but
   never ``filesystem.delete``" expressible and tamper-resistant.
2. **Default deny.** A capability that matches no allow pattern is forbidden.

Patterns support a trailing/segment ``*`` wildcard (glob-style, ``fnmatch``),
so ``filesystem.*`` matches ``filesystem.write`` but not ``network.http``.

The killer property for safety is :meth:`PermissionSet.intersect`: combining two
sets can only ever *narrow* what is allowed. That is what lets nested
``safety_context`` blocks de-escalate privilege but never escalate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, FrozenSet, Iterable


def _freeze(patterns: Iterable[str]) -> FrozenSet[str]:
    return frozenset(p.strip() for p in patterns if p and p.strip())


@dataclass(frozen=True)
class PermissionSet:
    """An immutable allow/deny set of capability patterns.

    Prefer the constructors :meth:`of`, :meth:`allow_all`, and :meth:`deny_all`
    over building one by hand.
    """

    allow: FrozenSet[str] = field(default_factory=frozenset)
    deny: FrozenSet[str] = field(default_factory=frozenset)

    # -- constructors -----------------------------------------------------
    @classmethod
    def of(cls, *allow: str, deny: Iterable[str] = ()) -> "PermissionSet":
        """Build from explicit allow patterns and optional deny patterns."""
        return cls(allow=_freeze(allow), deny=_freeze(deny))

    @classmethod
    def allow_all(cls) -> "PermissionSet":
        """Allow every capability (use only for trusted, top-level contexts)."""
        return cls(allow=frozenset({"*"}))

    @classmethod
    def deny_all(cls) -> "PermissionSet":
        """Allow nothing. The safe default for an untrusted sub-context."""
        return cls()

    # -- queries ----------------------------------------------------------
    def allows(self, capability: str) -> bool:
        """Return ``True`` iff *capability* is allowed and not denied."""
        cap = capability.strip()
        if not cap:
            return False
        if any(fnmatchcase(cap, p) for p in self.deny):
            return False
        return any(fnmatchcase(cap, p) for p in self.allow)

    # -- combinators ------------------------------------------------------
    def intersect(self, other: "PermissionSet") -> "PermissionSet":
        """Narrow ``self`` by ``other``: allow only what *both* allow.

        Denies are unioned (anything denied by either side stays denied), so the
        result is always at least as restrictive as each input. This is the
        monotonic de-escalation guarantee that makes nested contexts safe.
        """
        merged_deny = self.deny | other.deny
        # A pattern survives only if the other set would also allow everything
        # it can match. We approximate that soundly: keep patterns from each
        # side that the *other* side allows, which never widens either input.
        kept = {p for p in self.allow if other._allows_pattern(p)}
        kept |= {p for p in other.allow if self._allows_pattern(p)}
        return PermissionSet(allow=frozenset(kept), deny=merged_deny)

    def with_denied(self, *capabilities: str) -> "PermissionSet":
        """Return a copy that additionally denies *capabilities* (only narrows)."""
        return PermissionSet(allow=self.allow, deny=self.deny | _freeze(capabilities))

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> "dict[str, list[str]]":
        """A JSON-safe ``{"allow": [...], "deny": [...]}`` representation."""
        return {"allow": sorted(self.allow), "deny": sorted(self.deny)}

    @classmethod
    def from_dict(cls, data: "dict[str, Any]") -> "PermissionSet":
        """Rebuild a :class:`PermissionSet` from :meth:`to_dict` output.

        Lets ops define a capability policy declaratively (JSON/TOML/YAML) and
        load it, without the core needing a config format of its own.
        """
        return cls(allow=_freeze(data.get("allow", ())), deny=_freeze(data.get("deny", ())))

    # -- internals --------------------------------------------------------
    def _allows_pattern(self, pattern: str) -> bool:
        """Whether this set would allow a (possibly wildcard) pattern.

        ``"*"`` in the allow set permits any pattern; otherwise we require an
        exact or covering match. This is intentionally conservative so that
        :meth:`intersect` can never accidentally grant a capability.
        """
        if "*" in self.allow:
            cap = pattern
            return not any(fnmatchcase(cap, d) for d in self.deny)
        return self.allows(pattern) or pattern in self.allow

    def __str__(self) -> str:
        allow = ", ".join(sorted(self.allow)) or "(none)"
        deny = ", ".join(sorted(self.deny)) or "(none)"
        return f"PermissionSet(allow=[{allow}] deny=[{deny}])"
