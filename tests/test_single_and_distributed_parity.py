"""Parity tests: same safety behaviors in single-thread local and distributed modes.

Local  = classic ``safely(...)`` in one process (no backend / envelope).
Distributed = shared ``MemoryBackend`` + ``RunContext``, and/or gateway mint +
              ``CapabilityEnvelope`` hot-path verify.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from agent_safety import safely, tool
from agent_safety.core.exceptions import (
    LoopDetected,
    PermissionDenied,
    QuotaExceeded,
    RateLimitExceeded,
)
from agent_safety.distributed.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.distributed.envelope import CapabilityEnvelope, EnvelopeSigner
from agent_safety.distributed.gateway.client import GatewayClient
from agent_safety.distributed.gateway.server import GatewayConfig, PolicyGateway, serve_gateway
from agent_safety.distributed.policy_spec import PolicySpec
from agent_safety.distributed.run import RunContext


@tool
def read_file(path: str) -> str:
    return f"contents of {path}; email jane@corp.com"


@tool
def write_file(path: str, text: str) -> str:
    return f"wrote {len(text)} bytes to {path}"


@tool
def search(q: str) -> str:
    return f"results: {q}"


@tool
def summarize(text: str) -> str:
    return f"summary: {text[:40]}"


# ---------------------------------------------------------------------------
# Permissions — allow / deny
# ---------------------------------------------------------------------------

def test_local_allow_and_deny():
    with safely(allow="read_file", deny="write_file"):
        assert read_file("a.txt").startswith("contents")
        with pytest.raises(PermissionDenied):
            write_file("a.txt", "x")


def test_distributed_envelope_allow_and_deny():
    secret = b"parity-perm-signing-secret-32b!!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="t-perm",
        policy_hash="h",
        allowed_capabilities=("read_file",),
        capability="read_file",
    )
    with safely(envelope=env, envelope_keys={"default": secret}):
        assert read_file("a.txt").startswith("contents")
        with pytest.raises(PermissionDenied):
            write_file("a.txt", "x")


def test_distributed_backend_allow_and_deny():
    backend = MemoryBackend()
    with safely(
        allow="read_file",
        deny="write_file",
        backend=backend,
        run=RunContext.new(agent_id="w"),
    ):
        assert read_file("a.txt").startswith("contents")
        with pytest.raises(PermissionDenied):
            write_file("a.txt", "x")


# ---------------------------------------------------------------------------
# Call quotas
# ---------------------------------------------------------------------------

def test_local_calls_budget():
    with safely(allow="search", calls=2):
        search("a")
        search("b")
        with pytest.raises(QuotaExceeded):
            search("c")


def test_distributed_backend_calls_budget_single_thread():
    backend = MemoryBackend()
    task_id = uuid.uuid4().hex
    with safely(
        allow="search",
        calls=2,
        backend=backend,
        run=RunContext(task_id=task_id, agent_id="w", request_id="r1"),
    ):
        search("a")
        search("b")
        with pytest.raises(QuotaExceeded):
            search("c")


def test_distributed_backend_calls_budget_across_workers():
    """Two sequential workers share one task budget via backend."""
    backend = MemoryBackend()
    task_id = uuid.uuid4().hex
    with safely(
        allow="search",
        calls=2,
        backend=backend,
        run=RunContext(task_id=task_id, agent_id="w1", request_id="r1"),
    ):
        search("a")
    with safely(
        allow="search",
        calls=2,
        backend=backend,
        run=RunContext(task_id=task_id, agent_id="w2", request_id="r2"),
    ):
        search("b")
        with pytest.raises(QuotaExceeded):
            search("c")


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------

def test_local_rate_limit():
    with safely(allow="search", per_second=2):
        search("a")
        search("b")
        with pytest.raises(RateLimitExceeded):
            search("c")


def test_distributed_backend_rate_limit():
    backend = MemoryBackend()
    with safely(
        allow="search",
        per_second=2,
        backend=backend,
        run=RunContext.new(agent_id="w"),
    ):
        search("a")
        search("b")
        with pytest.raises(RateLimitExceeded):
            search("c")


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------

def test_local_no_repeats():
    with safely(allow="search", no_repeats=2):
        search("same")
        search("same")
        with pytest.raises(LoopDetected):
            search("same")


def test_distributed_backend_no_repeats_shared():
    backend = MemoryBackend()
    task_id = uuid.uuid4().hex
    limits = BudgetLimits(max_identical=2)
    sig = "search|same"
    backend.charge(BudgetCharge(task_id, "r1", sig), limits)
    backend.charge(BudgetCharge(task_id, "r2", sig), limits)
    with pytest.raises(LoopDetected):
        backend.charge(BudgetCharge(task_id, "r3", sig), limits)


def test_distributed_safely_no_repeats_via_backend():
    backend = MemoryBackend()
    with safely(
        allow="search",
        no_repeats=2,
        backend=backend,
        run=RunContext.new(agent_id="w"),
    ):
        search("same")
        search("same")
        with pytest.raises(LoopDetected):
            search("same")


# ---------------------------------------------------------------------------
# Output guards (local + distributed envelope path)
# ---------------------------------------------------------------------------

def test_local_hide_secrets():
    with safely(allow="read_file", hide_secrets=True):
        out = read_file("notes.txt")
        assert "jane@corp.com" not in out
        assert "REDACTED" in out


def test_distributed_envelope_hide_secrets():
    secret = b"parity-redact-signing-secret-32!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="t-redact",
        policy_hash="h",
        allowed_capabilities=("read_file",),
        capability="read_file",
    )
    with safely(
        envelope=env,
        envelope_keys={"default": secret},
        hide_secrets=True,
    ):
        out = read_file("notes.txt")
        assert "jane@corp.com" not in out
        assert "REDACTED" in out


# ---------------------------------------------------------------------------
# Handoff / narrow — local nest vs distributed envelope
# ---------------------------------------------------------------------------

def test_local_nested_narrowing():
    with safely(allow=["search", "summarize"], calls=10):
        search("q")
        with safely(allow="summarize", calls=3):
            summarize("doc")
            with pytest.raises(PermissionDenied):
                search("blocked")


def test_distributed_handoff_narrow_envelope():
    secret = b"parity-handoff-signing-secret-32"
    signer = EnvelopeSigner(secret)
    parent = PolicySpec(allow=("search", "summarize"), calls=10)
    child = parent.narrow(PolicySpec(allow=("summarize",), calls=3))
    env = signer.sign(
        task_id="t-handoff",
        policy_hash=child.policy_hash(),
        allowed_capabilities=child.allow or ("summarize",),
        capability="summarize",
    )
    with safely(envelope=env, envelope_keys={"default": secret}):
        assert summarize("doc").startswith("summary")
        with pytest.raises(PermissionDenied):
            search("blocked")


# ---------------------------------------------------------------------------
# Gateway mint → worker safely(envelope) end-to-end
# ---------------------------------------------------------------------------

@pytest.fixture
def live_gateway():
    import socket

    secret = b"parity-gateway-signing-secret!!"
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("search", "read_file"), calls=5, no_repeats=3)
    gw.config.registry.publish(spec)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = serve_gateway(gw, host="127.0.0.1", port=port)
    yield f"http://127.0.0.1:{port}", secret, spec, backend
    server.shutdown()


def test_gateway_mint_then_local_worker_runs_tool(live_gateway):
    base, secret, spec, _ = live_gateway
    client = GatewayClient(base)
    ctx = RunContext.new(agent_id="tool-worker")
    envelope = client.fetch_envelope({
        "task_id": ctx.task_id,
        "request_id": ctx.request_id,
        "capability": "search",
        "policy_spec": spec.to_dict(),
    })
    assert envelope is not None
    with safely(envelope=envelope, envelope_keys={"default": secret}, run=ctx):
        assert search("paris") == "results: paris"


def test_gateway_mint_denies_then_worker_cannot_run(live_gateway):
    base, secret, spec, _ = live_gateway
    client = GatewayClient(base)
    env = client.fetch_envelope({
        "task_id": "t",
        "request_id": "r",
        "capability": "write_file",
        "policy_spec": spec.to_dict(),
    })
    assert env is None  # mint denied — capability not in policy


def test_gateway_shared_budget_exhausts_across_mints(live_gateway):
    base, _, spec, backend = live_gateway
    client = GatewayClient(base)
    task_id = uuid.uuid4().hex
    # Use a calls-only policy so loop-guard signatures don't trip first.
    budget_spec = PolicySpec(allow=("search",), calls=3)
    for i in range(3):
        env = client.fetch_envelope({
            "task_id": task_id,
            "request_id": f"mint-{i}",
            "capability": "search",
            "policy_spec": budget_spec.to_dict(),
            "signature": f"unique-sig-{i}",
        })
        assert env is not None
    resp = client.mint({
        "task_id": task_id,
        "request_id": "mint-overflow",
        "capability": "search",
        "policy_spec": budget_spec.to_dict(),
        "signature": "unique-sig-overflow",
    })
    assert resp.ok is False
    assert "quota" in (resp.error or "").lower() or "exceed" in (resp.error or "").lower() or resp.error


# ---------------------------------------------------------------------------
# Multi-thread: shared backend (distributed concurrency)
# ---------------------------------------------------------------------------

def test_multithread_shared_backend_call_budget():
    backend = MemoryBackend()
    task_id = uuid.uuid4().hex
    errors: list[str] = []
    ok = 0
    lock = threading.Lock()

    def worker(idx: int) -> None:
        nonlocal ok
        try:
            with safely(
                allow="search",
                calls=3,
                backend=backend,
                run=RunContext(
                    task_id=task_id,
                    agent_id=f"w{idx}",
                    request_id=f"req-{idx}-{uuid.uuid4().hex[:8]}",
                ),
            ):
                search(f"q{idx}")
            with lock:
                ok += 1
        except QuotaExceeded:
            with lock:
                errors.append(f"quota-{idx}")
        except Exception as exc:
            with lock:
                errors.append(f"other-{idx}:{exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ok == 3
    assert len(errors) == 3
    assert all(e.startswith("quota-") for e in errors)


def test_multithread_org_isolation():
    backend = MemoryBackend()
    results = {"acme": 0, "globex": 0}
    lock = threading.Lock()

    def worker(org: str, idx: int) -> None:
        with safely(
            allow="search",
            calls=2,
            backend=backend,
            run=RunContext(
                task_id="same-task-id",
                agent_id=f"{org}-{idx}",
                request_id=f"{org}-{idx}",
                org_id=org,
            ),
        ):
            search(org)
        with lock:
            results[org] += 1

    threads = []
    for org in ("acme", "globex"):
        for i in range(2):
            threads.append(threading.Thread(target=worker, args=(org, i)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["acme"] == 2
    assert results["globex"] == 2

    # Third call per org should fail independently
    for org in ("acme", "globex"):
        with pytest.raises(QuotaExceeded):
            with safely(
                allow="search",
                calls=2,
                backend=backend,
                run=RunContext(
                    task_id="same-task-id",
                    agent_id=f"{org}-overflow",
                    request_id=f"{org}-overflow",
                    org_id=org,
                ),
            ):
                search(org)


def test_threadpool_parallel_branches_one_budget():
    backend = MemoryBackend()
    task_id = uuid.uuid4().hex
    limits = BudgetLimits(max_calls=4)
    outcomes: list[str] = []

    def branch(i: int) -> str:
        try:
            backend.charge(
                BudgetCharge(
                    task_id=task_id,
                    request_id=f"branch-{i}",
                    signature=f"branch-{i}",
                ),
                limits,
            )
            return "ok"
        except QuotaExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(branch, i) for i in range(8)]
        for f in as_completed(futures):
            outcomes.append(f.result())

    assert outcomes.count("ok") == 4
    assert outcomes.count("denied") == 4


# ---------------------------------------------------------------------------
# Envelope + backend together (mint charged call; tokens on backend)
# ---------------------------------------------------------------------------

def test_envelope_plus_backend_runs_tool_without_double_call_charge():
    secret = b"parity-combo-signing-secret-32b"
    backend = MemoryBackend()
    signer = EnvelopeSigner(secret)
    task_id = uuid.uuid4().hex
    # Pre-charge one call as the gateway mint would.
    backend.charge(
        BudgetCharge(task_id=task_id, request_id="mint-1", signature="mint"),
        BudgetLimits(max_calls=2),
    )
    env = signer.sign(
        task_id=task_id,
        policy_hash="h",
        allowed_capabilities=("search",),
        capability="search",
    )
    # With envelope, BackendQuota.charge_calls=False — tool should still run.
    with safely(
        envelope=env,
        envelope_keys={"default": secret},
        backend=backend,
        calls=2,
        run=RunContext(task_id=task_id, agent_id="w", request_id="worker-1"),
    ):
        assert search("x") == "results: x"
    # One more mint-style charge still fits; a third does not.
    backend.charge(
        BudgetCharge(task_id=task_id, request_id="mint-2", signature="mint"),
        BudgetLimits(max_calls=2),
    )
    with pytest.raises(QuotaExceeded):
        backend.charge(
            BudgetCharge(task_id=task_id, request_id="mint-3", signature="mint"),
            BudgetLimits(max_calls=2),
        )


# ---------------------------------------------------------------------------
# Single-thread local still works under explicit local mode
# ---------------------------------------------------------------------------

def test_local_mode_unaffected_by_clean_env(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "local")
    with safely(allow="search", calls=1):
        assert search("ok") == "results: ok"
        with pytest.raises(QuotaExceeded):
            search("again")


def test_enforce_mode_blocks_local_without_envelope(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    with pytest.raises(PermissionDenied, match="envelope required"):
        with safely(allow="search"):
            search("x")


def test_enforce_mode_gateway_envelope_works(monkeypatch, live_gateway):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    base, secret, spec, _ = live_gateway
    client = GatewayClient(base)
    ctx = RunContext.new(agent_id="enforced-worker")
    envelope = client.fetch_envelope({
        "task_id": ctx.task_id,
        "request_id": ctx.request_id,
        "capability": "search",
        "policy_spec": spec.to_dict(),
    })
    assert isinstance(envelope, CapabilityEnvelope)
    with safely(envelope=envelope, envelope_keys={"default": secret}, run=ctx):
        assert search("enforced") == "results: enforced"
