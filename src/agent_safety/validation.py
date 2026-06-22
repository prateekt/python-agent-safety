"""Validate tool-call arguments against a tool's declared JSON Schema.

:mod:`agent_safety.schema` derives the schema a model is told to follow; this
closes the loop by checking that the arguments the model *actually sent* conform
before the tool runs. It is a small, dependency-free validator for exactly the
shapes :mod:`agent_safety.schema` emits — ``type``, ``enum``, ``required``,
``items``, ``additionalProperties``, ``minimum`` / ``maximum``, and ``anyOf`` —
not a full JSON-Schema engine.

A non-conforming call raises a :class:`~agent_safety.exceptions.GuardViolation`
(stage ``"input"``), so when it fires inside ``ToolRegistry.safe_dispatch`` the
malformed call is reported back to the model instead of reaching your function as
the wrong type. Enable it per tool with ``@registry.tool(..., validate=True)``.
"""

from __future__ import annotations

from typing import Any, List, Mapping

from .exceptions import GuardViolation


def _type_ok(value: Any, json_type: str) -> bool:
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "array":
        return isinstance(value, (list, tuple))
    if json_type == "object":
        return isinstance(value, Mapping)
    if json_type == "null":
        return value is None
    return True  # unknown type keyword -> don't constrain


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _here(path: str) -> str:
    return path or "value"


def _branch_ok(value: Any, schema: Mapping[str, Any]) -> bool:
    errors: List[str] = []
    _validate(value, schema, "", errors)
    return not errors


def _validate(value: Any, schema: Mapping[str, Any], path: str, errors: List[str]) -> None:
    if "anyOf" in schema:
        if not any(_branch_ok(value, s) for s in schema["anyOf"]):
            errors.append(f"{_here(path)} matches none of the allowed schemas")
        return

    json_type = schema.get("type")
    if json_type is not None and not _type_ok(value, json_type):
        errors.append(f"{_here(path)} should be {json_type}, got {type(value).__name__}")
        return  # wrong type -> further checks are noise

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{_here(path)} must be one of {schema['enum']}")
    if "minimum" in schema and _is_number(value) and value < schema["minimum"]:
        errors.append(f"{_here(path)} must be >= {schema['minimum']}")
    if "maximum" in schema and _is_number(value) and value > schema["maximum"]:
        errors.append(f"{_here(path)} must be <= {schema['maximum']}")

    if json_type == "object" and isinstance(value, Mapping):
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"missing required {(path + '.' + required if path else required)!r}")
        for key, item in value.items():
            sub = props.get(key)
            child = f"{path}.{key}" if path else key
            if sub is not None:
                _validate(item, sub, child, errors)
            elif additional is False:
                errors.append(f"unexpected property {key!r}")
            elif isinstance(additional, Mapping):
                _validate(item, additional, child, errors)

    if json_type == "array" and isinstance(value, (list, tuple)):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for i, item in enumerate(value):
                _validate(item, items, f"{path}[{i}]", errors)


def validate_args(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate *arguments* against *schema*; raise ``GuardViolation`` if invalid."""
    errors: List[str] = []
    _validate(dict(arguments), schema, "", errors)
    if errors:
        raise GuardViolation(
            "schema_validation", "input", "; ".join(errors), value=dict(arguments),
        )
