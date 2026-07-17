"""Deprecated entry point — use ``python -m agent_safety.distributed.gateway``."""

import runpy

runpy.run_module("agent_safety.distributed.gateway", run_name="__main__")
