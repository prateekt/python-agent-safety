import pytest

from agent_safety import (
    MaxLength,
    PermissionDenied,
    PermissionSet,
    check_input,
    current_policy,
    is_allowed,
    require,
    safety_context,
)
from agent_safety.exceptions import GuardViolation


def test_default_is_deny_all():
    # Outside any context, nothing is permitted (fail safe).
    assert not is_allowed("filesystem.read")
    with pytest.raises(PermissionDenied):
        require("filesystem.read")


def test_context_grants_then_restores():
    with safety_context(PermissionSet.of("filesystem.read")):
        assert is_allowed("filesystem.read")
        require("filesystem.read")
    assert not is_allowed("filesystem.read")


def test_nested_context_can_only_narrow():
    with safety_context(PermissionSet.of("filesystem.*", "network.http")):
        assert is_allowed("network.http")
        with safety_context(PermissionSet.of("filesystem.read")):
            assert is_allowed("filesystem.read")
            assert not is_allowed("filesystem.write")
            assert not is_allowed("network.http")
        # outer privileges restored
        assert is_allowed("network.http")


def test_nested_context_cannot_escalate():
    with safety_context(PermissionSet.of("filesystem.read")):
        with safety_context(PermissionSet.allow_all()):
            # Even asking for allow_all cannot widen the parent's grant.
            assert is_allowed("filesystem.read")
            assert not is_allowed("shell.exec")


def test_policy_restored_on_exception():
    try:
        with safety_context(PermissionSet.of("filesystem.read")):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not is_allowed("filesystem.read")


def test_guards_accumulate_in_nested_contexts():
    with safety_context(PermissionSet.allow_all(), input_guards=[MaxLength(10)]):
        assert check_input("short") == "short"
        with safety_context(input_guards=[MaxLength(3)]):
            # both the outer (10) and inner (3) caps now apply
            with pytest.raises(GuardViolation):
                check_input("toolong")
        # inner guard gone, outer remains
        assert check_input("under ten") == "under ten"


def test_current_policy_returns_effective():
    with safety_context(PermissionSet.of("a.b")):
        assert current_policy().allows("a.b")
