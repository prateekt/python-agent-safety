import pytest

from agent_safety import (
    NetworkAllowlist,
    PathBoundary,
    PermissionSet,
    Stage,
    guarded_tool,
    safety_context,
)
from agent_safety.exceptions import GuardViolation

# -- PathBoundary ---------------------------------------------------------

def test_path_boundary_allows_inside(tmp_path):
    g = PathBoundary(str(tmp_path))
    assert g.check("notes.txt", Stage.INPUT) == "notes.txt"
    abs_inside = str(tmp_path / "sub" / "a.txt")
    assert g.check(abs_inside, Stage.INPUT) == abs_inside


def test_path_boundary_blocks_dotdot_traversal(tmp_path):
    g = PathBoundary(str(tmp_path))
    with pytest.raises(GuardViolation):
        g.check("../../etc/passwd", Stage.INPUT)


def test_path_boundary_blocks_absolute_outside(tmp_path):
    g = PathBoundary(str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    with pytest.raises(GuardViolation):
        g.check(str(tmp_path / "outside.txt"), Stage.INPUT)


def test_path_boundary_blocks_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside)  # symlink that points out of the sandbox
    g = PathBoundary(str(root))
    with pytest.raises(GuardViolation):
        g.check("escape/secret.txt", Stage.INPUT)


def test_path_boundary_root_itself_toggle(tmp_path):
    assert PathBoundary(str(tmp_path)).check(str(tmp_path), Stage.INPUT) == str(tmp_path)
    strict = PathBoundary(str(tmp_path), allow_root_itself=False)
    with pytest.raises(GuardViolation):
        strict.check(str(tmp_path), Stage.INPUT)


def test_path_boundary_passes_non_string(tmp_path):
    g = PathBoundary(str(tmp_path))
    assert g.check(123, Stage.INPUT) == 123


def test_path_boundary_in_guarded_tool(tmp_path):
    @guarded_tool("filesystem.read", input_guards=[PathBoundary(str(tmp_path))])
    def read_file(path: str) -> str:
        return f"read {path}"

    with safety_context(PermissionSet.of("filesystem.read")):
        assert read_file("ok.txt") == "read ok.txt"
        with pytest.raises(GuardViolation):
            read_file("../../../etc/passwd")


# -- NetworkAllowlist -----------------------------------------------------

def test_network_allows_listed_https_host():
    g = NetworkAllowlist(["api.weather.com"])
    url = "https://api.weather.com/v1/forecast"
    assert g.check(url, Stage.INPUT) == url


def test_network_blocks_unlisted_host():
    g = NetworkAllowlist(["api.weather.com"])
    with pytest.raises(GuardViolation):
        g.check("https://evil.example/steal", Stage.INPUT)


def test_network_blocks_http_by_default():
    g = NetworkAllowlist(["api.weather.com"])
    with pytest.raises(GuardViolation):
        g.check("http://api.weather.com/x", Stage.INPUT)


def test_network_blocks_other_scheme():
    g = NetworkAllowlist(["host"], schemes=["https"])
    with pytest.raises(GuardViolation):
        g.check("ftp://host/file", Stage.INPUT)


def test_network_subdomains():
    g = NetworkAllowlist(["example.com"])
    assert g.check("https://api.example.com/x", Stage.INPUT)
    strict = NetworkAllowlist(["example.com"], allow_subdomains=False)
    with pytest.raises(GuardViolation):
        strict.check("https://api.example.com/x", Stage.INPUT)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.5/internal",
        "http://localhost/secret",
    ],
)
def test_network_blocks_private_targets(url):
    # block_private wins even when the scheme/host would otherwise be allowed.
    g = NetworkAllowlist(schemes=["http", "https"])
    with pytest.raises(GuardViolation):
        g.check(url, Stage.INPUT)


def test_network_private_allowed_when_disabled():
    g = NetworkAllowlist(schemes=["http"], block_private=False)
    url = "http://127.0.0.1:8080/x"
    assert g.check(url, Stage.INPUT) == url


def test_network_passes_non_url_and_non_string():
    g = NetworkAllowlist(["host"])
    assert g.check("just some text", Stage.INPUT) == "just some text"
    assert g.check(42, Stage.INPUT) == 42


def test_network_empty_schemes_rejected():
    with pytest.raises(ValueError):
        NetworkAllowlist(["host"], schemes=[])
