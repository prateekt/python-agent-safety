import pytest

from agent_safety import SecretScanner, Stage, UnicodeSanitizer
from agent_safety.exceptions import GuardViolation

# -- SecretScanner --------------------------------------------------------

@pytest.mark.parametrize(
    "text,label",
    [
        ("creds AKIAIOSFODNN7EXAMPLE here", "AWS_ACCESS_KEY"),
        ("token ghp_" + "a" * 36, "GITHUB_TOKEN"),
        ("AIza" + "B" * 35, "GOOGLE_API_KEY"),
        ("eyJabc.eyJdef.sigXYZ_-", "JWT"),
        ("-----BEGIN RSA PRIVATE KEY-----", "PRIVATE_KEY"),
    ],
)
def test_secret_scanner_redacts(text, label):
    g = SecretScanner()
    out = g.check(text, Stage.OUTPUT)
    assert f"[REDACTED:{label}]" in out


def test_secret_scanner_block_mode():
    g = SecretScanner(block=True)
    with pytest.raises(GuardViolation):
        g.check("key AKIAIOSFODNN7EXAMPLE", Stage.OUTPUT)


def test_secret_scanner_passes_clean_text_and_non_string():
    g = SecretScanner()
    assert g.check("nothing secret here", Stage.OUTPUT) == "nothing secret here"
    assert g.check(123, Stage.OUTPUT) == 123


# -- UnicodeSanitizer -----------------------------------------------------

def test_unicode_sanitizer_strips_zero_width():
    g = UnicodeSanitizer()
    dirty = "hel​lo‍ wor﻿ld"   # zero-width space, ZWJ, BOM
    assert g.check(dirty, Stage.INPUT) == "hello world"


def test_unicode_sanitizer_strips_tag_chars():
    g = UnicodeSanitizer()
    hidden = "run" + "".join(chr(c) for c in (0xE0073, 0xE0075))  # tag chars
    assert g.check(hidden, Stage.INPUT) == "run"


def test_unicode_sanitizer_block_mode():
    g = UnicodeSanitizer(block=True)
    with pytest.raises(GuardViolation):
        g.check("inv‮isible", Stage.INPUT)   # bidi override


def test_unicode_sanitizer_passes_clean_and_non_string():
    g = UnicodeSanitizer()
    assert g.check("plain ascii", Stage.INPUT) == "plain ascii"
    assert g.check(42, Stage.INPUT) == 42
