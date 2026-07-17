"""Agent handoff with a narrowed capability envelope."""

from __future__ import annotations

import uuid

from agent_safety import safely, tool
from agent_safety.distributed.envelope import EnvelopeSigner, EnvelopeVerifier
from agent_safety.distributed.policy_spec import PolicySpec
from agent_safety.distributed.run import RunContext


@tool
def search(q: str) -> str:
    return f"results for {q}"


@tool
def summarize(text: str) -> str:
    return f"summary: {text[:40]}"


def main() -> None:
    secret = b"handoff-signing-secret-32bytes!"
    signer = EnvelopeSigner(secret)
    verifier = EnvelopeVerifier({"default": secret})
    task_id = uuid.uuid4().hex

    parent = PolicySpec(allow=("search", "summarize"), calls=10)
    child = parent.narrow(PolicySpec(allow=("summarize",), calls=3))

    # Supervisor delegates summarize-only subtask to worker B
    envelope = signer.sign(
        task_id=task_id,
        policy_hash=child.policy_hash(),
        allowed_capabilities=child.allow or ("summarize",),
        capability="summarize",
    )

    ctx = RunContext(task_id=task_id, agent_id="worker-b", request_id=uuid.uuid4().hex)
    with safely(envelope=envelope, envelope_keys={"default": secret}, run=ctx):
        verifier.verify(envelope)
        print(summarize("long document about agent safety"))


if __name__ == "__main__":
    main()
