"""Tests for distributed rollout mode helpers."""

from __future__ import annotations

import pytest

from agent_safety.distributed import (
    DistributedMode,
    distributed_mode,
    should_enforce_envelope,
    should_shadow_envelope,
)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("AGENT_SAFETY_DISTRIBUTED", raising=False)


def test_default_local_mode(clean_env):
    assert distributed_mode() == DistributedMode.LOCAL
    assert not should_enforce_envelope()
    assert not should_shadow_envelope()


def test_enforce_mode(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    assert distributed_mode() == DistributedMode.ENFORCE
    assert should_enforce_envelope()
    assert should_shadow_envelope()


def test_shadow_mode(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "shadow")
    assert distributed_mode() == DistributedMode.SHADOW
    assert not should_enforce_envelope()
    assert should_shadow_envelope()


def test_invalid_mode_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "not-a-mode")
    assert distributed_mode() == DistributedMode.LOCAL
