"""Constitutional guards: safety rules in plain English, judged by a model.

Some policies are hard to write as a permission or a regex — "never email a
customer without prior consent", "don't delete anything in production". A
:class:`ConstitutionGate` lets you state the rule in words and hand the judgement
to a model (or any callable): before a matching tool runs, the judge is asked
whether the action complies, and a "no" raises
:class:`~agent_safety.exceptions.ConstitutionViolation`.

    def judge(action, rule) -> bool:
        # ask your model: does `action` obey `rule`? return True if it does.
        ...

    with safely(allow="email.send",
                rule="never email a customer without prior consent",
                judge=judge):
        send_email(...)            # judged against the rule before it runs

The judge is **provider-agnostic** — it's just a callable, so plug in Claude,
OpenAI, Gemini, or a deterministic stub. Because the verdict is *probabilistic*,
treat it as defence in depth: keep the hard ``allow=`` / limits underneath so a
judge slip still can't do damage. The judge may be sync or async (async judges
require ``@guarded_async_tool``).
"""

from __future__ import annotations

import inspect
from fnmatch import fnmatchcase
from typing import Any, Callable, Iterable, List, Union

# A judge decides whether an action obeys a rule — truthy = compliant.
Judge = Callable[[Any, str], Any]


class ConstitutionGate:
    """Require a model judge to clear each matching tool call against *rules*.

    Args:
        rules: One rule, or several; the call must obey every matching rule.
        judge: ``(action, rule) -> bool``; truthy means the action complies.
        require: Capability patterns (glob ``*``) the rules apply to.
    """

    def __init__(
        self,
        rules: Union[str, Iterable[str]],
        judge: Judge,
        *,
        require: Iterable[str] = ("*",),
    ):
        raw = [rules] if isinstance(rules, str) else list(rules)
        self.rules: List[str] = [r.strip() for r in raw if r and r.strip()]
        if not self.rules:
            raise ValueError("a ConstitutionGate needs at least one rule")
        self.judge = judge
        self.patterns = tuple(p.strip() for p in require if p and p.strip()) or ("*",)
        self.is_async = inspect.iscoroutinefunction(judge)
        self.name = "constitution(" + "; ".join(self.rules) + ")"

    def covers(self, capability: str) -> bool:
        return any(fnmatchcase(capability, p) for p in self.patterns)
