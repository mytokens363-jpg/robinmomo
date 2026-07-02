"""
Low-level MCP client for the Robinhood Trading MCP server.

Speaks JSON-RPC 2.0 over streamable-HTTP (the transport OpenClaw's client and the
RH MCP both support; SSE is the documented fallback). This module builds and would
send the request; the actual network send is the single STUB that lights up once
the Agentic account is authenticated and oauth.py returns a live token.

Confirm against a real tool-surface audit on day one:
    ask the connected agent to list its tools, diff against TOOLS below.
"""
from __future__ import annotations
import json, os, uuid

from proxy import oauth

# Community-documented RH equity tool surface (June 2026). MUST be confirmed by a
# live tool-list audit once connected — these come from third-party write-ups, not
# a direct pull from RH's tool registry.
TOOLS = {
    "read": {"get_accounts", "get_portfolio", "get_equity_positions",
             "get_equity_quotes", "get_equity_orders", "search"},
    "watchlist": {"get_watchlists", "add_to_watchlist", "update_watchlist"},
    "trade": {"review_equity_order", "place_equity_order", "cancel_equity_order"},
}
ALL_TOOLS = set().union(*TOOLS.values())

TIMEOUT_S = float(os.environ.get("RH_MCP_TIMEOUT", "30"))


class MCPError(Exception):
    pass


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
    """Send one tool call to the RH MCP and return the parsed result.

    STUB: builds the authenticated request, then stops at the network boundary.
    Replace the marked block with a real streamable-HTTP POST once authenticated.
    """
    if tool not in ALL_TOOLS:
        raise MCPError(f"unknown tool '{tool}' (not in confirmed surface)")

    request = _build_request(tool, args)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               **oauth.auth_headers()}            # bearer injected here
    url = oauth.MCP_TRADING_URL

    # ---- NETWORK SEND (stub) ----------------------------------------------
    # import httpx
    # with httpx.Client(timeout=TIMEOUT_S) as c:
    #     r = c.post(url, json=request, headers=headers)
    #     r.raise_for_status()
    #     return _parse_result(r.json())
    raise NotImplementedError(
        f"network send not wired for '{tool}'. Authenticate the account, drop in the "
        f"httpx POST above (target {url}), then run the tool-surface audit.")
