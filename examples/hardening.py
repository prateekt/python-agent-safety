"""End-to-end walkthrough of the hardening layers.

    python examples/hardening.py

Everything here is the easy API — plain ``safely`` keywords — plus two sandbox
guards from ``agent_safety.core`` attached per-tool:

* PathBoundary       — keep a filesystem tool inside its sandbox
* NetworkAllowlist   — keep a network tool off private/SSRF targets
* per_second= / no_repeats= — bound how fast / how repetitively the agent acts
* ask=               — require a "yes" before a sensitive call
* explain=           — require the agent to justify itself
* rollback()         — undo irreversible actions when a later step fails

Everything below runs offline with no API keys.
"""

from agent_safety import ApprovalDenied, safely, tool
from agent_safety.core import (
    NetworkAllowlist,
    PathBoundary,
    record_thought,
    rollback,
    thought_trace,
    trace_span,
)
from agent_safety.core.exceptions import (
    ExplanationRequired,
    GuardViolation,
    LoopDetected,
    RateLimitExceeded,
)


@tool("filesystem.read", input_guards=[PathBoundary("/srv/data")])
def read_file(path: str) -> str:
    return f"<contents of {path}>"


@tool(
    "network.http",
    # Allow http too, so the metadata URL below is caught by the SSRF/private-IP
    # check rather than the (https-only) scheme check — that's the point of the demo.
    input_guards=[NetworkAllowlist(["api.weather.com"], schemes=["http", "https"])],
)
def http_get(url: str) -> str:
    return f"<response from {url}>"


@tool("search.run")
def search(query: str) -> str:
    return f"results for {query!r}"


@tool("shell.exec")
def run_shell(cmd: str) -> str:
    return f"$ {cmd}\n(ok)"


def main() -> None:
    print("== 1. PathBoundary confines a filesystem tool ==")
    with safely(allow="filesystem.read"):
        print("inside sandbox: ", read_file("reports/q3.txt"))
        try:
            read_file("../../etc/passwd")
        except GuardViolation as e:
            print("traversal blocked:", e)

    print("\n== 2. NetworkAllowlist blocks SSRF / off-list hosts ==")
    with safely(allow="network.http"):
        print("allowed host:   ", http_get("https://api.weather.com/forecast"))
        for bad in ("https://evil.example/x", "http://169.254.169.254/latest/meta-data"):
            try:
                http_get(bad)
            except GuardViolation as e:
                print("blocked:        ", e)

    print("\n== 3. per_second= caps bursts ==")
    with safely(allow="search.run", per_second=2):
        print("call 1:", search("a"))
        print("call 2:", search("b"))
        try:
            search("c")
        except RateLimitExceeded as e:
            print("call 3 blocked:", e)

    print("\n== 4. no_repeats= breaks a stuck agent ==")
    with safely(allow="search.run", no_repeats=2):
        for i in range(4):
            try:
                search("same query")  # identical args every time
                print(f"call {i + 1}: ok")
            except LoopDetected as e:
                print(f"call {i + 1} blocked:", e)
                break

    print("\n== 5. ask= requires a yes before acting ==")
    # A scripted approver: approve reads, reject shell.
    def approver(action) -> bool:
        decision = action.capability != "shell.exec"
        print(f"   [approver] {action.tool}{action.args} -> {'approve' if decision else 'reject'}")
        return decision

    with safely(allow=["shell.exec", "filesystem.read"], ask=approver):
        print("approved read:  ", read_file("reports/q3.txt"))
        try:
            run_shell("rm -rf /")
        except ApprovalDenied as e:
            print("rejected shell: ", e)

    print("\n== 6. explain= requires the agent to justify itself ==")
    with safely(allow="shell.exec", explain="shell.exec"):
        with thought_trace() as trace, trace_span("cleanup"):
            record_thought("the build dir has stale artifacts; I'll clear them")
            print("with rationale:", run_shell(
                "rm -rf build/*", rationale="Clearing stale build output before a fresh build"))
        print("recorded thoughts:", [t.text for t in trace])
        try:
            run_shell("rm -rf /")  # no rationale
        except ExplanationRequired as e:
            print("blocked (no why):", e)

    print("\n== 7. rollback() unwinds a failed multi-step action ==")
    ledger = []  # stand-in for external side effects
    try:
        with rollback() as tx:
            ledger.append("record")
            tx.on_undo(lambda: ledger.remove("record"))
            ledger.append("email")
            tx.on_undo(lambda: ledger.remove("email"))
            print("   before failure, ledger:", ledger)
            raise RuntimeError("payment declined")  # third step fails
    except RuntimeError as e:
        print("   step failed:", e)
    print("   after rollback, ledger:", ledger, "(both effects compensated)")


if __name__ == "__main__":
    main()
