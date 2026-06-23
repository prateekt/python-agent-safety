"""CI gate for the published attack scorecard.

The benchmark in `benchmarks/attack_suite.py` is a marketing/credibility artifact:
"N/M attacks contained." This test enforces it, so the claim can never silently
regress — if a guard breaks, a scenario "escapes" and this fails.
"""

from benchmarks.attack_suite import run


def test_every_attack_is_contained_and_every_legit_action_runs():
    results = run()

    escaped = [r for r in results if not r.ok]
    assert not escaped, "scenarios not handled correctly: " + ", ".join(
        f"{r.name} ({r.detail})" for r in escaped
    )

    attacks = [r for r in results if r.kind == "attack"]
    legit = [r for r in results if r.kind == "legit"]

    assert len(attacks) >= 13, "the attack suite shrank unexpectedly"
    assert len(legit) >= 4, "the legitimate-action controls shrank unexpectedly"
    assert all(r.ok for r in attacks)        # nothing dangerous got through
    assert all(r.ok for r in legit)          # nothing legitimate was blocked


def test_scenarios_cover_the_main_owasp_risks():
    covered = {r.owasp.split()[0] for r in run() if r.kind == "attack"}
    for risk in ("LLM01", "LLM02", "LLM05", "LLM06", "LLM07", "LLM10"):
        assert risk in covered, f"no attack scenario covers {risk}"
