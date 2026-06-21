"""Derive a tool's JSON-Schema from its Python signature.

Declaring a tool to an LLM means writing a JSON Schema that, almost always, just
restates what the function signature *already says* — the parameter names, their
types, which are required, and a sentence of description. That is duplication a
Python developer shouldn't have to maintain.

:func:`tool_schema` reads the signature with :mod:`inspect`, resolves the
annotations with :func:`typing.get_type_hints` (keeping ``Annotated`` metadata),
and emits the ``{"type": "object", ...}`` parameter schema for you.
:func:`tool_description` pulls the summary line from the docstring. Both are used
automatically by :meth:`ToolRegistry.tool` when you omit ``parameters=`` /
``description=``; an explicit value always wins.

Supported annotations (everything is standard library — no Pydantic):

* ``str``/``int``/``float``/``bool`` → the matching JSON primitive
* ``list[T]`` / ``List[T]`` / ``Sequence[T]`` → ``array`` with ``items``
* ``dict[K, V]`` / ``Dict`` / ``Mapping`` → ``object`` (+ ``additionalProperties``)
* ``Optional[T]`` / ``T | None`` → schema of ``T`` (and, with no default, still required)
* ``Union[...]`` → ``anyOf``
* ``Literal["a", "b"]`` and ``enum.Enum`` subclasses → ``enum``
* ``Annotated[T, "a description"]`` → the description, and
  ``Annotated[T, Param(description=..., enum=..., minimum=..., maximum=...)]`` for
  richer constraints.

A parameter with no default is **required**; one with a default is optional (and
the default is included when it is JSON-safe). ``self``/``cls`` and
``*args``/``**kwargs`` are skipped. Unknown/`Any` types yield an unconstrained
schema rather than guessing.
"""

from __future__ import annotations

import collections.abc as cabc
import enum
import inspect
import types
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, get_args, get_origin

# ``T | None`` (PEP 604) is a distinct runtime origin from ``typing.Union`` on
# 3.10+, and absent on 3.9. Look it up dynamically so this stays import-clean and
# type-checks the same whether mypy targets 3.9 or a newer interpreter.
_UnionType = getattr(types, "UnionType", None)
_UNION_ORIGINS: Tuple[Any, ...] = (
    (typing.Union, _UnionType) if _UnionType is not None else (typing.Union,)
)

_PRIMITIVES: Dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


@dataclass(frozen=True)
class Param:
    """Per-parameter schema metadata, attached to a type via ``Annotated``.

        def search(
            query: Annotated[str, "what to look for"],
            limit: Annotated[int, Param(description="max rows", minimum=1, maximum=100)] = 10,
        ) -> list: ...
    """

    description: str = ""
    enum: Optional[List[Any]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


def _is_annotated(tp: Any) -> bool:
    return hasattr(tp, "__metadata__")


def _json_safe(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, list, dict))


def _apply_param(schema: Dict[str, Any], p: Param) -> None:
    if p.description:
        schema.setdefault("description", p.description)
    if p.enum is not None:
        schema["enum"] = list(p.enum)
    if p.minimum is not None:
        schema["minimum"] = p.minimum
    if p.maximum is not None:
        schema["maximum"] = p.maximum


def _enum_schema(values: List[Any]) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"enum": list(values)}
    if values:
        primitive = _PRIMITIVES.get(type(values[0]))
        if primitive:
            schema["type"] = primitive
    return schema


def _schema_for_type(tp: Any) -> Dict[str, Any]:
    """Map a single Python type annotation to a JSON-Schema fragment."""
    if tp is Any or tp is None or tp is inspect.Parameter.empty:
        return {}

    # Annotated[T, ...] — schema of T, enriched by the metadata.
    if _is_annotated(tp):
        base, *meta = get_args(tp)
        schema = _schema_for_type(base)
        for m in meta:
            if isinstance(m, str):
                schema.setdefault("description", m)
            elif isinstance(m, Param):
                _apply_param(schema, m)
        return schema

    origin = get_origin(tp)

    # Optional[T] / Union[...] / T | None
    if origin in _UNION_ORIGINS:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _schema_for_type(non_none[0])
        return {"anyOf": [_schema_for_type(a) for a in non_none]}

    # Literal["a", "b"] -> enum
    if origin is typing.Literal:
        return _enum_schema(list(get_args(tp)))

    # enum.Enum subclass -> enum of member values
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return _enum_schema([e.value for e in tp])

    # Sequences -> array (but never treat str/bytes as a sequence here)
    if tp in (list, tuple) or origin in (list, tuple, cabc.Sequence):
        args = get_args(tp)
        schema = {"type": "array"}
        if args and args[0] is not Ellipsis:
            schema["items"] = _schema_for_type(args[0])
        return schema

    # Mappings -> object
    if tp is dict or origin in (dict, cabc.Mapping):
        args = get_args(tp)
        schema = {"type": "object"}
        if len(args) == 2:
            schema["additionalProperties"] = _schema_for_type(args[1])
        return schema

    # Primitives (exact match; note bool is its own key, so it never collapses
    # into int even though it subclasses it).
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}

    # Unknown — leave unconstrained rather than guess.
    return {}


def tool_schema(func: Callable[..., Any]) -> Dict[str, Any]:
    """Build the JSON-Schema ``parameters`` object for *func*'s signature."""
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except Exception:  # forward refs that can't be resolved -> fall back to raw
        hints = dict(getattr(func, "__annotations__", {}))

    properties: Dict[str, Any] = {}
    required: List[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        sub = _schema_for_type(hints.get(name, param.annotation))
        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif _json_safe(param.default):
            sub.setdefault("default", param.default)
        properties[name] = sub

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool_description(func: Callable[..., Any], explicit: str = "") -> str:
    """Return *explicit* if given, else the docstring's summary paragraph."""
    if explicit:
        return explicit
    doc = inspect.getdoc(func)
    if not doc:
        return ""
    # First blank-line-delimited paragraph, collapsed to a single line.
    summary = doc.strip().split("\n\n", 1)[0]
    return " ".join(line.strip() for line in summary.splitlines()).strip()
