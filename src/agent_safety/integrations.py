"""Provider-neutral glue between the safety core and any tool-calling LLM.

The safety primitives don't care which model is driving — a tool is just a
Python function wrapped with the active :class:`~agent_safety.policy.Policy`.
The only things that differ across Claude, OpenAI, and Gemini are:

1. the **JSON shape** of a tool/function declaration, and
2. how each SDK reports a requested call (Anthropic ``tool_use`` block,
   OpenAI ``tool_calls`` with a JSON-string ``arguments``, Gemini
   ``function_call`` part with a dict ``args``).

:class:`ToolRegistry` absorbs both. You declare each tool once; it emits
schemas in whichever **dialect** your provider expects, and :meth:`dispatch`
runs the call through the full safety pipeline regardless of where it came
from. There is **no SDK dependency** here — you pass the name + arguments your
provider's response already gave you, and format the result back with
:meth:`tool_result`.

    registry = ToolRegistry()

    @registry.tool("filesystem.read", description="Read a UTF-8 text file.",
                   parameters={"type": "object",
                               "properties": {"path": {"type": "string"}},
                               "required": ["path"]})
    def read_file(path: str) -> str:
        return open(path).read()

    tools = registry.schemas("openai")          # or "anthropic" / "gemini"
    # ... send `tools` to the model, read back a tool call ...
    result = registry.dispatch(name, arguments) # safety-checked, dialect-agnostic
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .context import in_context, is_allowed
from .decorators import guarded_tool
from .exceptions import AgentSafetyError
from .guards import Guard
from .schema import tool_description, tool_schema
from .validation import validate_args

DIALECTS = ("anthropic", "openai", "gemini")


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by a model, normalized across providers."""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a mapping or attribute, so SDK objects and dicts both work."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_tool_calls(dialect: str, response: Any) -> List[ToolCall]:
    """Extract the tool calls from a provider's raw response, dialect-agnostic.

    Accepts the response as a dict (or an SDK object with matching attributes) and
    returns a list of :class:`ToolCall` ready to feed to :meth:`ToolRegistry.safe_dispatch`.
    No SDK import — it reads only the documented response shape of each provider.
    """
    calls: List[ToolCall] = []
    if dialect == "anthropic":
        for block in _get(response, "content", []) or []:
            if _get(block, "type") == "tool_use":
                calls.append(ToolCall(
                    str(_get(block, "id", "")), str(_get(block, "name", "")),
                    dict(_get(block, "input", {}) or {}),
                ))
        return calls
    if dialect == "openai":
        choices = _get(response, "choices", []) or []
        message = _get(choices[0], "message", {}) if choices else {}
        for call in _get(message, "tool_calls", []) or []:
            fn = _get(call, "function", {})
            raw = _get(fn, "arguments", "{}")
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            calls.append(ToolCall(str(_get(call, "id", "")), str(_get(fn, "name", "")), args))
        return calls
    if dialect == "gemini":
        candidates = _get(response, "candidates", []) or []
        content = _get(candidates[0], "content", {}) if candidates else {}
        for part in _get(content, "parts", []) or []:
            fn = _get(part, "function_call", None) or _get(part, "functionCall", None)
            if fn is not None:
                calls.append(ToolCall(
                    "", str(_get(fn, "name", "")), dict(_get(fn, "args", {}) or {}),
                ))
        return calls
    raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")


class ToolSpec:
    """A registered tool: its safety capability, JSON schema, and guarded fn."""

    def __init__(
        self,
        name: str,
        capability: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable[..., object],
        validate: bool = False,
    ):
        self.name = name
        self.capability = capability
        self.description = description
        self.parameters = parameters
        self.func = func  # already wrapped with @guarded_tool
        self.validate = validate

    # -- per-dialect schema -------------------------------------------------
    def schema(self, dialect: str) -> Dict[str, Any]:
        if dialect == "anthropic":
            return {
                "name": self.name,
                "description": self.description,
                "input_schema": self.parameters,
            }
        if dialect == "openai":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                },
            }
        if dialect == "gemini":
            return {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")


class ToolRegistry:
    """Holds guarded tools and speaks every provider's tool dialect."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    # -- registration -------------------------------------------------------
    def tool(
        self,
        capability: str,
        *,
        name: Optional[str] = None,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        input_guards: Iterable[Guard] = (),
        output_guards: Iterable[Guard] = (),
        validate: bool = False,
        idempotent: bool = False,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Decorator: register a function as a guarded, schema-carrying tool.

        When ``parameters`` or ``description`` are omitted they are inferred from
        the function's signature and docstring (see :mod:`agent_safety.schema`);
        an explicit value always wins. Pass ``validate=True`` to check each call's
        arguments against the schema before dispatch, or ``idempotent=True`` to
        cache the result of identical calls to a pure tool.
        """

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            params = parameters if parameters is not None else tool_schema(func)
            desc = tool_description(func, description)
            guarded = guarded_tool(
                capability, input_guards=input_guards,
                output_guards=output_guards, idempotent=idempotent,
            )(func)
            tool_name = name or func.__name__
            self._tools[tool_name] = ToolSpec(
                tool_name, capability, desc, params, guarded, validate
            )
            return guarded

        return decorator

    # -- schema export ------------------------------------------------------
    def schemas(self, dialect: str, *, allowed_only: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Return the ``tools`` value to hand the provider, in *dialect* shape.

        ``"anthropic"`` / ``"openai"`` → a list of tool definitions.
        ``"gemini"`` → a one-element list wrapping ``function_declarations``,
        ready to assign to the Gemini ``tools`` parameter.

        Inside an active ``safely(...)`` block this returns only the tools the policy
        *allows*, so one ``allow=`` governs both what the model is offered and what
        may actually run — no listing tools twice. Outside a block every registered
        tool is returned; pass ``allowed_only=True``/``False`` to force it either way.
        """
        if allowed_only is None:
            allowed_only = in_context()
        tools = list(self._tools.values())
        if allowed_only:
            tools = [t for t in tools if is_allowed(t.capability)]
        specs = [t.schema(dialect) for t in tools]
        if dialect == "gemini":
            return [{"function_declarations": specs}]
        if dialect in ("anthropic", "openai"):
            return specs
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, name: str, arguments: Any) -> object:
        """Run the named tool through the full safety pipeline.

        *arguments* may be a mapping (Anthropic ``input`` / Gemini ``args``) or a
        JSON string (OpenAI ``tool_calls[].function.arguments``). Permission,
        guards, quota, and audit are all enforced by the wrapped tool. Raises
        :class:`KeyError` for an unknown tool and
        :class:`~agent_safety.exceptions.AgentSafetyError` if safety blocks it.
        """
        if name not in self._tools:
            raise KeyError(f"no registered tool named {name!r}")
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must decode to an object/dict")
        spec = self._tools[name]
        if spec.validate:
            validate_args(spec.parameters, arguments)  # raises GuardViolation if invalid
        return spec.func(**arguments)

    # -- result formatting --------------------------------------------------
    def tool_result(
        self,
        dialect: str,
        call_id: str,
        name: str,
        result: object,
        *,
        is_error: bool = False,
    ) -> Dict[str, Any]:
        """Build the provider-native message/part that returns *result* to the model."""
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        if dialect == "anthropic":
            block: Dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": text,
            }
            if is_error:
                block["is_error"] = True
            return block
        if dialect == "openai":
            return {"role": "tool", "tool_call_id": call_id, "content": text}
        if dialect == "gemini":
            payload: Dict[str, Any] = {"error": text} if is_error else {"result": result}
            return {"functionResponse": {"name": name, "response": payload}}
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")

    def safe_dispatch(self, dialect: str, call_id: str, name: str, arguments: Any) -> Dict[str, Any]:
        """Dispatch and format in one step, turning a safety block into an error result.

        Returns a provider-native tool-result message in every case, so a denied
        or guarded call is reported back to the model instead of crashing the loop.
        """
        try:
            result = self.dispatch(name, arguments)
            return self.tool_result(dialect, call_id, name, result)
        except (AgentSafetyError, KeyError, TypeError, ValueError) as exc:
            return self.tool_result(dialect, call_id, name, str(exc), is_error=True)
