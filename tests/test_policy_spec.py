"""Tests for PolicySpec and PolicyRegistry."""

from agent_safety.distributed.policy_spec import PolicyRegistry, PolicySpec


def test_policy_spec_hash_stable():
    spec = PolicySpec(allow=("search",), calls=10)
    assert spec.policy_hash() == spec.policy_hash()


def test_policy_spec_narrow():
    parent = PolicySpec(allow=("*"), calls=100)
    child = PolicySpec(allow=("search",), calls=10)
    narrowed = parent.narrow(child)
    assert narrowed.calls == 10
    assert "search" in narrowed.allow


def test_registry_publish_resolve():
    reg = PolicyRegistry()
    spec = PolicySpec(allow=("fs.read",), calls=5)
    phash = reg.publish(spec)
    resolved = reg.resolve(phash)
    assert resolved is not None
    assert resolved.calls == 5


def test_from_safely_kwargs():
    spec = PolicySpec.from_safely_kwargs(allow="search", calls=10, no_repeats=3)
    assert spec.allow == ("search",)
    assert spec.calls == 10
    assert spec.no_repeats == 3
