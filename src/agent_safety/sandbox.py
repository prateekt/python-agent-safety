"""Sandbox guards: confine *where* a tool may reach.

The guards in :mod:`agent_safety.guards` inspect the *content* of a value
(length, banned phrases, secrets). The two guards here instead constrain the
*resource* a value points at:

* :class:`PathBoundary` keeps a filesystem path inside a directory you choose —
  blocking ``../`` traversal and symlink escapes so a ``read_file`` tool can't
  wander out of its sandbox.
* :class:`NetworkAllowlist` keeps a URL on an approved host and scheme — the
  first line of defence against an agent being talked into a server-side
  request forgery (SSRF) against your cloud metadata endpoint or intranet.

Both implement the :class:`~agent_safety.guards.Guard` protocol, so they slot
into ``input_guards=[...]`` on a tool exactly like any other guard and raise
:class:`~agent_safety.exceptions.GuardViolation` to block. Like the rest of the
library they are **standard-library only**.

They act on **string** values and pass anything else through untouched, so when
the decorator threads every argument through them only the path/URL argument is
actually checked.

Honest scope: these are pre-flight *intent* checks, not a kernel sandbox. They
resolve symlinks and reject private IP literals, but they cannot defeat a
time-of-check/time-of-use race or DNS rebinding on their own. Treat them as a
strong default that belongs *behind* a real OS/network sandbox, not instead of one.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Iterable
from urllib.parse import urlsplit

from .exceptions import GuardViolation
from .guards import Stage


class PathBoundary:
    """Confine a filesystem path argument to *root* (and its subtree).

    A path is interpreted relative to *root* (or, if absolute, taken as-is),
    fully resolved with :func:`os.path.realpath` — which collapses ``..`` and
    follows symlinks — and rejected unless the result is *root* itself or lives
    underneath it. That single real-path comparison defeats both ``../../etc``
    traversal and a symlink inside the sandbox that points outside it.

    Args:
        root: The directory the tool is allowed to touch.
        allow_root_itself: Whether the boundary directory *itself* is a valid
            target (default ``True``); set ``False`` to require a path strictly
            inside it.

    The value is returned **unchanged** on success (the tool still receives the
    path it was given); only the decision to allow or block depends on the
    resolved location.
    """

    def __init__(self, root: str, *, allow_root_itself: bool = True):
        if not root:
            raise ValueError("root must be a non-empty path")
        # Resolve the root once so every check compares against a stable anchor.
        self.root = os.path.realpath(root)
        self.allow_root_itself = allow_root_itself
        self.name = f"path_boundary({self.root!r})"

    def _resolve(self, path: str) -> str:
        candidate = path if os.path.isabs(path) else os.path.join(self.root, path)
        return os.path.realpath(candidate)

    def check(self, value: object, stage: Stage) -> object:
        if not isinstance(value, str):
            return value
        resolved = self._resolve(value)
        if resolved == self.root:
            if self.allow_root_itself:
                return value
            raise GuardViolation(
                self.name, stage.value,
                "path resolves to the boundary root itself", value=value,
            )
        # ``commonpath`` raises ValueError across drives/relative mixes; treat
        # that as "outside" rather than letting it crash the guard.
        try:
            inside = os.path.commonpath([self.root, resolved]) == self.root
        except ValueError:
            inside = False
        if not inside:
            raise GuardViolation(
                self.name, stage.value,
                f"path escapes sandbox root (resolved to {resolved!r})", value=value,
            )
        return value


# Hostnames that always denote the local machine; blocked when SSRF protection
# is on regardless of how they resolve.
_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class NetworkAllowlist:
    """Allow a URL argument only to an approved host and scheme.

    By default a non-empty *hosts* list is an allowlist: a URL whose host is not
    in it (nor a subdomain of one, when ``allow_subdomains``) is blocked. The
    scheme must be in *schemes* (``https`` only by default). When
    ``block_private`` is on (the default) a URL pointing at a loopback,
    link-local, private, or otherwise non-public IP **literal** — or at
    ``localhost`` — is blocked even if its host is allowlisted, which stops the
    most common SSRF target (e.g. ``http://169.254.169.254/`` cloud metadata).

    Args:
        hosts: Allowed hostnames. Empty means "any host" (rely on
            ``block_private`` / ``schemes`` alone).
        schemes: Allowed URL schemes (lower-case, no ``://``).
        allow_subdomains: Treat an allowed ``example.com`` as also permitting
            ``api.example.com`` (default ``True``).
        block_private: Reject private/loopback/link-local/reserved IP literals
            and ``localhost`` (default ``True``).

    Non-string values, and strings that don't parse as a URL with a network
    location, pass through unchanged — so the guard only constrains the URL
    argument of a network tool, not its other parameters. The value is returned
    unchanged on success.

    Note: host checks use the literal host in the URL; this guard does **not**
    resolve DNS, so it cannot by itself stop a public name that resolves to a
    private address (DNS rebinding). Enforce that in your HTTP client too.
    """

    def __init__(
        self,
        hosts: Iterable[str] = (),
        *,
        schemes: Iterable[str] = ("https",),
        allow_subdomains: bool = True,
        block_private: bool = True,
    ):
        self.hosts = frozenset(h.lower().strip() for h in hosts if h and h.strip())
        self.schemes = frozenset(s.lower().strip() for s in schemes if s and s.strip())
        if not self.schemes:
            raise ValueError("at least one scheme must be allowed")
        self.allow_subdomains = allow_subdomains
        self.block_private = block_private
        shown = ", ".join(sorted(self.hosts)) or "(any)"
        self.name = f"network_allowlist([{shown}])"

    def _host_allowed(self, host: str) -> bool:
        if not self.hosts:
            return True
        if host in self.hosts:
            return True
        if self.allow_subdomains:
            return any(host.endswith("." + h) for h in self.hosts)
        return False

    @staticmethod
    def _private_ip(host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return not ip.is_global

    def check(self, value: object, stage: Stage) -> object:
        if not isinstance(value, str):
            return value
        parts = urlsplit(value.strip())
        # Not a URL we recognise (no scheme + no host) -> some other argument.
        if not parts.scheme and not parts.netloc:
            return value
        if parts.scheme.lower() not in self.schemes:
            raise GuardViolation(
                self.name, stage.value,
                f"scheme {parts.scheme!r} not in {sorted(self.schemes)}", value=value,
            )
        host = (parts.hostname or "").lower()
        if not host:
            raise GuardViolation(
                self.name, stage.value, "URL has no host", value=value,
            )
        if self.block_private and (host in _LOCAL_HOSTNAMES or self._private_ip(host)):
            raise GuardViolation(
                self.name, stage.value,
                f"host {host!r} is a private/loopback address (possible SSRF)",
                value=value,
            )
        if not self._host_allowed(host):
            raise GuardViolation(
                self.name, stage.value,
                f"host {host!r} is not in the allowlist", value=value,
            )
        return value
