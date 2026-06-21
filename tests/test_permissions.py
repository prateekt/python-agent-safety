from agent_safety import PermissionSet


def test_default_deny():
    ps = PermissionSet.of("filesystem.read")
    assert ps.allows("filesystem.read")
    assert not ps.allows("filesystem.write")
    assert not ps.allows("shell.exec")


def test_wildcard_allow():
    ps = PermissionSet.of("filesystem.*")
    assert ps.allows("filesystem.read")
    assert ps.allows("filesystem.write")
    assert not ps.allows("network.http")


def test_deny_wins_over_allow():
    ps = PermissionSet.of("filesystem.*", deny=["filesystem.delete"])
    assert ps.allows("filesystem.write")
    assert not ps.allows("filesystem.delete")


def test_allow_all():
    ps = PermissionSet.allow_all()
    assert ps.allows("anything.at.all")


def test_deny_all():
    ps = PermissionSet.deny_all()
    assert not ps.allows("filesystem.read")


def test_intersect_only_narrows():
    broad = PermissionSet.of("filesystem.*", "network.http")
    narrow = PermissionSet.of("filesystem.read")
    combined = broad.intersect(narrow)
    assert combined.allows("filesystem.read")
    assert not combined.allows("filesystem.write")
    assert not combined.allows("network.http")


def test_intersect_cannot_widen():
    # A child set that "allows" more than the parent cannot grant it.
    parent = PermissionSet.of("filesystem.read")
    child = PermissionSet.of("filesystem.*", "shell.exec")
    combined = parent.intersect(child)
    assert combined.allows("filesystem.read")
    assert not combined.allows("filesystem.write")
    assert not combined.allows("shell.exec")


def test_intersect_with_allow_all():
    combined = PermissionSet.allow_all().intersect(PermissionSet.of("filesystem.read"))
    assert combined.allows("filesystem.read")
    assert not combined.allows("shell.exec")


def test_intersect_unions_denies():
    a = PermissionSet.of("filesystem.*", deny=["filesystem.delete"])
    b = PermissionSet.of("filesystem.*", deny=["filesystem.write"])
    combined = a.intersect(b)
    assert not combined.allows("filesystem.delete")
    assert not combined.allows("filesystem.write")
    assert combined.allows("filesystem.read")


def test_with_denied():
    ps = PermissionSet.allow_all().with_denied("shell.exec")
    assert ps.allows("filesystem.read")
    assert not ps.allows("shell.exec")
