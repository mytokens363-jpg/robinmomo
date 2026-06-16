"""
Local MCP proxy skeleton for robinmomo.

Why this exists: OpenClaw's MCP client speaks streamable-HTTP but has no interactive
OAuth flow — only static headers. Robinhood's Agentic Trading MCP requires OAuth.
This proxy is the bridge: it owns the OAuth token (via proxy/oauth.py), exposes a
LOCAL MCP surface to OpenClaw / the runner with no auth needed locally, and forwards
tool calls upstream to agent.robinhood.com with the bearer attached.

It is ALSO the second, independent enforcement point for the allow-list and a hard
kill check — so a bug in the runner cannot place an off-list or post-kill order.
Defense in depth: runner enforces, proxy re-enforces.

STATUS: skeleton. The upstream tool names/schemas (place_order, get_account, etc.)
are PLACEHOLDERS to confirm against Robinhood's MCP once the account is live. No
network call is implemented; forward() raises until wired.
"""
from __future__ import annotations
import os

from strategy.config import UNIVERSE
from proxy import oauth

# Tools the proxy will REFUSE to forward unless the symbol is in the universe.
# Confirm exact RH tool names when the account exists.
ORDER_TOOLS = {"place_order", "place_equity_order", "submit_order"}
KILL_FLAG_ENV = "ROBINMOMO_PROXY_KILL"   # set to "1" to hard-block all forwarding


class ProxyKill(Exception):
    pass


def _assert_live_allowed() -> None:
    if os.environ.get(KILL_FLAG_ENV) == "1":
        raise ProxyKill("proxy kill flag set — all forwarding blocked")


def _validate_order(tool: str, args: dict) -> None:
    """Independent allow-list + sanity gate, mirroring live/rails.py."""
    sym = args.get("symbol") or args.get("ticker")
    if sym is None:
        raise ProxyKill(f"{tool}: no symbol in args — refusing")
    if sym not in UNIVERSE:
        raise ProxyKill(f"{tool}: {sym} not in allow-list — refusing")
    if str(args.get("side", "")).lower() in {"sell_short", "short"}:
        raise ProxyKill(f"{tool}: shorting not permitted")


def forward(tool: str, args: dict) -> dict:
    """Forward a single MCP tool call upstream to Robinhood.

    STUB: builds the authenticated request but does not send it. Replace the body
    with a real streamable-HTTP MCP client call once tool schemas are confirmed.
    """
    _assert_live_allowed()
    if tool in ORDER_TOOLS:
        _validate_order(tool, args)          # reject BEFORE fetching any credential

    headers = oauth.auth_headers()           # bearer injected here (only for valid calls)
    url = oauth.MCP_TRADING_URL

    # TODO: send MCP request to `url` with `headers`, transport=streamable-http.
    raise NotImplementedError(
        f"proxy.forward('{tool}') not wired. Confirm RH MCP tool schema, then "
        f"implement the streamable-HTTP client call to {url}.")


if __name__ == "__main__":
    # Smoke test the gates without any network.
    for tool, args, label in [
        ("place_order", {"symbol": "XLK", "side": "buy", "qty": 1}, "allowed symbol"),
        ("place_order", {"symbol": "TSLA", "side": "buy", "qty": 1}, "off-list symbol"),
        ("place_order", {"symbol": "XLF", "side": "short", "qty": 1}, "short attempt"),
    ]:
        try:
            forward(tool, args)
        except (ProxyKill, NotImplementedError, FileNotFoundError) as e:
            print(f"[{label:16}] {type(e).__name__}: {str(e).splitlines()[0]}")
