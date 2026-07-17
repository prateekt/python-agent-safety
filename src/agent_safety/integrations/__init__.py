"""Provider and protocol glue: tool registries, dialects, and MCP.

    from agent_safety.integrations import ToolRegistry, parse_tool_calls, guard_mcp
"""

from .mcp import SafeMCP, guard_mcp
from .providers import DIALECTS, ToolCall, ToolRegistry, ToolSpec, parse_tool_calls

__all__ = [
    "ToolRegistry", "ToolSpec", "ToolCall", "parse_tool_calls", "DIALECTS",
    "guard_mcp", "SafeMCP",
]
