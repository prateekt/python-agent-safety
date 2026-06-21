"""Guards: composable checks/transforms for prompts, inputs, and outputs.

A *guard* inspects a value flowing through the agent and either

* returns the value unchanged,
* returns a **sanitized** version (e.g. with secrets redacted), or
* raises :class:`~agent_safety.exceptions.GuardViolation` to block it entirely.

Guards run at three *stages*:

* ``Stage.PROMPT``  — the system/user prompt about to be sent to the model.
* ``Stage.INPUT``   — arguments about to be handed to a tool the agent invoked.
* ``Stage.OUTPUT``  — text/data coming back from the model or a tool.

All built-in guards use only the standard library, so they run anywhere with no
extra dependencies. They are deliberately simple heuristics — a starting point
you extend, not a complete security boundary on their own.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable, List, Protocol, runtime_checkable

from .exceptions import GuardViolation


class Stage(str, Enum):
    """Where in the agent loop a guard is being applied."""

    PROMPT = "prompt"
    INPUT = "input"
    OUTPUT = "output"


@runtime_checkable
class Guard(Protocol):
    """The guard protocol. Anything with a matching ``check`` is a guard."""

    name: str

    def check(self, value: object, stage: Stage) -> object:
        """Return *value* (possibly transformed) or raise ``GuardViolation``."""
        ...


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else str(value)


class MaxLength:
    """Block values whose text representation exceeds *limit* characters.

    Long inputs are a common vector for prompt-stuffing and runaway cost, so a
    hard cap is a cheap, high-value guard.
    """

    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("limit must be positive")
        self.limit = limit
        self.name = f"max_length({limit})"

    def check(self, value: object, stage: Stage) -> object:
        text = _as_text(value)
        if len(text) > self.limit:
            raise GuardViolation(
                self.name, stage.value,
                f"length {len(text)} exceeds limit {self.limit}", value=value,
            )
        return value


class DenyPattern:
    """Block any value whose text matches *pattern* (case-insensitive)."""

    def __init__(self, pattern: str, reason: str = "matched a denied pattern"):
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.reason = reason
        self.name = f"deny_pattern({pattern!r})"

    def check(self, value: object, stage: Stage) -> object:
        if self.regex.search(_as_text(value)):
            raise GuardViolation(self.name, stage.value, self.reason, value=value)
        return value


# Heuristic phrases commonly seen in prompt-injection / jailbreak attempts.
_INJECTION_PATTERNS = [
    r"ignore (all|any|the)? ?(previous|prior|above) (instructions|prompts?)",
    r"disregard (the|all|your)? ?(system|previous) (prompt|instructions?)",
    r"you are (now|no longer) .{0,40}(dan|developer mode|unrestricted)",
    r"reveal (your|the) (system )?(prompt|instructions)",
    r"print (your|the) (system )?(prompt|instructions|api[_ ]?key)",
    r"\bsudo\b.{0,20}\bmode\b",
]


class PromptInjectionGuard:
    """Heuristically block common prompt-injection / jailbreak phrasings.

    This catches well-known phrasings ("ignore previous instructions", "reveal
    your system prompt", …). It is a tripwire, not a guarantee; pair it with
    least-privilege :class:`~agent_safety.permissions.PermissionSet` so a missed
    injection still cannot do anything dangerous.
    """

    def __init__(self, extra_patterns: Iterable[str] = ()):
        patterns = list(_INJECTION_PATTERNS) + list(extra_patterns)
        self.regex = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
        self.name = "prompt_injection"

    def check(self, value: object, stage: Stage) -> object:
        match = self.regex.search(_as_text(value))
        if match:
            raise GuardViolation(
                self.name, stage.value,
                f"possible prompt injection: {match.group(0)!r}", value=value,
            )
        return value


# Redaction patterns: (label, compiled regex). Order matters — broad last.
_PII_PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|api|key|token|secret)[-_][A-Za-z0-9_]{12,}\b", re.IGNORECASE)),
    ("PHONE", re.compile(r"\b\+?\d{1,3}[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
]


class RedactPII:
    """Sanitizing guard: replace likely secrets/PII with ``[REDACTED:LABEL]``.

    Unlike the blocking guards, this **transforms** the value and lets it
    through — ideal on the OUTPUT stage so an agent never echoes a leaked key or
    customer email back to a caller or into logs.
    """

    def __init__(self, placeholder: str = "[REDACTED:{label}]"):
        self.placeholder = placeholder
        self.name = "redact_pii"

    def check(self, value: object, stage: Stage) -> object:
        if not isinstance(value, str):
            return value
        text = value
        for label, regex in _PII_PATTERNS:
            text = regex.sub(self.placeholder.format(label=label), text)
        return text


class Compose:
    """Run several guards in order, threading the (possibly transformed) value."""

    def __init__(self, guards: Iterable[Guard]):
        self.guards: List[Guard] = list(guards)
        self.name = "compose(" + ", ".join(g.name for g in self.guards) + ")"

    def check(self, value: object, stage: Stage) -> object:
        for guard in self.guards:
            value = guard.check(value, stage)
        return value


def run_guards(guards: Iterable[Guard], value: object, stage: Stage) -> object:
    """Apply *guards* to *value* in order, returning the final transformed value."""
    for guard in guards:
        value = guard.check(value, stage)
    return value
