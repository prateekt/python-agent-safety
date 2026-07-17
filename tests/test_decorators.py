"""The deprecated ``guarded_tool`` decorator still works (it delegates to the
same pipeline as ``@tool``) but warns — this file is the intentional coverage
for that legacy surface."""

import pytest

from agent_safety import PermissionDenied
from agent_safety.core.context import safety_context
from agent_safety.core.exceptions import GuardViolation
from agent_safety.core.guards import MaxLength, RedactPII
from agent_safety.core.permissions import PermissionSet
from agent_safety.core.pipeline import guarded_tool

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

with pytest.warns(DeprecationWarning, match="use @tool"):

    @guarded_tool("filesystem.read")
    def read_note(path: str) -> str:
        # Pretend file contents that contain a secret.
        return f"contents of {path} -- contact admin@corp.com"

    @guarded_tool("shell.exec", input_guards=[MaxLength(20)])
    def run_command(cmd: str) -> str:
        return f"ran: {cmd}"


def test_denied_without_context():
    with pytest.raises(PermissionDenied):
        read_note("notes.txt")


def test_allowed_within_context():
    with safety_context(PermissionSet.of("filesystem.read")):
        assert read_note("notes.txt").startswith("contents of notes.txt")


def test_output_guard_applied():
    with safety_context(PermissionSet.of("filesystem.read"), output_guards=[RedactPII()]):
        out = read_note("notes.txt")
        assert "admin@corp.com" not in out
        assert "[REDACTED:EMAIL]" in out


def test_tool_specific_input_guard():
    with safety_context(PermissionSet.of("shell.exec")):
        assert run_command("ls") == "ran: ls"
        with pytest.raises(GuardViolation):
            run_command("this command is definitely way too long")


def test_capability_attribute_exposed():
    assert read_note.__agent_capability__ == "filesystem.read"


def test_narrowed_context_revokes_tool():
    with safety_context(PermissionSet.of("filesystem.read", "shell.exec")):
        assert read_note("a").startswith("contents")
        with safety_context(PermissionSet.of("filesystem.read")):
            # shell.exec dropped here
            with pytest.raises(PermissionDenied):
                run_command("ls")
