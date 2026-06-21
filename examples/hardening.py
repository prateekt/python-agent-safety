"""End-to-end walkthrough of the v0.3 hardening layers.

    python examples/hardening.py

Shows the constructs added on top of permissions/guards/quota:

* PathBoundary    — keep a filesystem tool inside its sandbox
* NetworkAllowlist — keep a network tool off private/SSRF targets
* RateLimit + LoopGuard — bound how fast / how repetitively the agent acts
* ApprovalGate    — require a human "yes" before a sensitive call
* rollback()      — undo irreversible actions when a later step fails

Everything below runs offline with no API keys.
"""

from agent_safety import (
    ApprovalDenied,
    ApprovalGate,
    LoopDetected,
    LoopGuard,
    NetworkAllowlist,
    PathBoundary,
    PermissionSet,
    RateLimit,
    RateLimitExceeded,
    guarded_tool,
    rollback,
    safety_context,
)
from agent_safety.exceptions import GuardViolation


@guarded_tool("filesystem.read", input_guards=[PathBoundary("/srv/data")])
def read_file(path: str) -> str:
    return f"<contents of {path}>"


@guarded_tool(
    "network.http",
    # Allow http too, so the metadata URL below is caught by the SSRF/private-IP
    # check rather than the (https-only) scheme check — that's the point of the demo.
    input_guards=[NetworkAllowlist(["api.weather.com"], schemes=["http", "https"])],
)
def http_get(url: str) -> str:
    return f"<response from {url}>"


@guarded_tool("search.run")
def search(query: str) -> str:
    return f"results for {query!r}"


@guarded_tool("shell.exec")
def run_shell(cmd: str) -> str:
    return f"$ {cmd}\n(ok)"


def main() -> None:
    print("== 1. PathBoundary confines a filesystem tool ==")
    with safety_context(PermissionSet.of("filesystem.read")):
        print("inside sandbox: ", read_file("reports/q3.txt"))
        try:
            read_file("../../etc/passwd")
        except GuardViolation as e:
            print("traversal blocked:", e)

    print("\n== 2. NetworkAllowlist blocks SSRF / off-list hosts ==")
    with safety_context(PermissionSet.of("network.http")):
        print("allowed host:   ", http_get("https://api.weather.com/forecast"))
        for bad in ("https://evil.example/x", "http://169.254.169.254/latest/meta-data"):
            try:
                http_get(bad)
            except GuardViolation as e:
                print("blocked:        ", e)

    print("\n== 3. RateLimit caps bursts ==")
    with safety_context(PermissionSet.of("search.run"), rate_limit=RateLimit(per_second=2)):
        print("call 1:", search("a"))
        print("call 2:", search("b"))
        try:
            search("c")
        except RateLimitExceeded as e:
            print("call 3 blocked:", e)

    print("\n== 4. LoopGuard breaks a stuck agent ==")
    with safety_context(PermissionSet.of("search.run"), loop_guard=LoopGuard(max_identical=2)):
        for i in range(4):
            try:
                search("same query")  # identical args every time
                print(f"call {i + 1}: ok")
            except LoopDetected as e:
                print(f"call {i + 1} blocked:", e)
                break

    print("\n== 5. ApprovalGate requires a human yes ==")
    # A scripted approver: approve reads, reject shell.
    def approver(req) -> bool:
        decision = req.capability != "shell.exec"
        print(f"   [approver] {req.tool}{req.args} -> {'approve' if decision else 'reject'}")
        return decision

    gate = ApprovalGate(require=["shell.exec", "filesystem.read"], approver=approver)
    with safety_context(
        PermissionSet.of("shell.exec", "filesystem.read"), approval=gate
    ):
        print("approved read:  ", read_file("reports/q3.txt"))
        try:
            run_shell("rm -rf /")
        except ApprovalDenied as e:
            print("rejected shell: ", e)

    print("\n== 6. rollback() unwinds a failed multi-step action ==")
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
