"""
Low-level MCP client for the Robinhood Trading MCP server.

Path 1 (session-bound official MCP): the transport is the Robinhood MCP connected
to a live Claude Code session. This module does NOT make its own httpx call — the
Robinhood Agentic auth is session-bound and no standalone headless token exists.

Execution model: deterministic Python (rails + proxy gates + order building)
computes the intended (tool, args) call. call_tool validates the tool name against
ALL_TOOLS, then raises Path1Error carrying (tool, args). The in-session Claude
that is driving the runner observes the error and dispatches the equivalent
mcp__robinhood-trading__<tool> call, feeding the result back to the calling
helper. Gates run in Python first — the session is the wire, never the decider.

Confirm against a real tool-surface audit on day one:
    ask the connected agent to list its tools, diff against TOOLS below.
"""
from __future__ import annotations
import json, os, uuid

# Community-documented RH equity tool surface (June 2026). MUST be confirmed by a
# live tool-list audit once connected — these come from third-party write-ups, not
# a direct pull from RH's tool registry.
TOOLS = {
    "read": {"get_accounts", "get_portfolio", "get_equity_positions",
             "get_equity_quotes", "get_equity_orders", "get_equity_tradability",
             "search"},
    "watchlist": {"get_watchlists", "add_to_watchlist", "update_watchlist"},
    "trade": {"review_equity_order", "place_equity_order", "cancel_equity_order"},
}
ALL_TOOLS = set().union(*TOOLS.values())

TIMEOUT_S = float(os.environ.get("RH_MCP_TIMEOUT", "30"))


class MCPError(Exception):
    pass


class Path1Error(Exception):
    """Raised by call_tool to hand off to the in-session Claude MCP.

    Path 1 has no direct Python transport — MCP calls route through the
    Robinhood MCP connected to the live Claude Code session. The message
    embeds the intended (tool, args) so an in-session driver can execute
    mcp__robinhood-trading__<tool> with the exact validated arguments and
    feed the parsed result back to the calling helper."""


def _build_request(tool: str, args: dict) -> dict:
    """JSON-RPC 2.0 tools/call envelope."""
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


def _parse_result(payload: dict) -> dict:
    """Extract the tool result from a JSON-RPC response, raising on error."""
    if "error" in payload:
        raise MCPError(f"MCP error: {payload['error']}")
    result = payload.get("result", {})
    # MCP tool results arrive as a content list; find structured/text content.
    content = result.get("content", [])
    for block in content:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except (json.JSONDecodeError, KeyError):
                return {"text": block.get("text", "")}
    return result


def call_tool(tool: str, args: dict) -> dict:
    """Validate the tool + args and hand off to the in-session MCP driver.

    Path 1: this function never makes a network call. The tool-surface allow-
    list still runs here — anything outside our confirmed set is rejected
    BEFORE the driver sees it. The Path1Error carries the exact (tool, args)
    the driver should dispatch as mcp__robinhood-trading__<tool>."""
    if tool not in ALL_TOOLS:
        raise MCPError(f"unknown tool '{tool}' (not in confirmed surface)")
    raise Path1Error(
        f"path1-dispatch: tool={tool} args={args}. "
        f"Route to mcp__robinhood-trading__{tool} via the in-session Claude MCP.")
