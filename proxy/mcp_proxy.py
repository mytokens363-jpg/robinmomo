"""
Robinhood Trading MCP proxy for robinmomo.

Bridges the local runner / OpenClaw to Robinhood's Agentic Trading MCP. Holds the
OAuth token (proxy/oauth.py), forwards tool calls via proxy/client.py, and is the
SECOND independent enforcement point (after live/rails.py) for the allow-list, the
no-short rule, and a hard kill flag.

Trade safety: order placement goes through review_then_place(), which calls
review_equity_order FIRST, inspects the pre-trade warnings RH returns, and only
then calls place_equity_order. That native simulate-before-execute step is a third
safety layer on top of the runner rails and this proxy's allow-list.

Tool names are the community-documented June 2026 surface and MUST be confirmed by
a live tool-surface audit before arming. See proxy/client.py.
"""
from __future__ import annotations
import os

from strategy.config import UNIVERSE
from proxy import client

ORDER_TOOL = "place_equity_order"
REVIEW_TOOL = "review_equity_order"
CANCEL_TOOL = "cancel_equity_order"
KILL_FLAG_ENV = "ROBINMOMO_PROXY_KILL"     # "1" hard-blocks ALL forwarding

# warning keywords in a review result that BLOCK placement outright.
# tune against real review_equity_order output once observed.
BLOCKING_WARNINGS = {"insufficient", "rejected", "not_allowed", "margin",
                     "pattern_day_trader", "restricted", "halted"}


class ProxyKill(Exception):
    pass


def _assert_live_allowed() -> None:
    if os.environ.get(KILL_FLAG_ENV) == "1":
        raise ProxyKill("proxy kill flag set — all forwarding blocked")


def _symbol_of(args: dict) -> str | None:
    return args.get("symbol") or args.get("ticker") or args.get("instrument")


def _validate_order(tool: str, args: dict) -> None:
    """Independent allow-list + no-short gate, mirroring live/rails.py."""
    sym = _symbol_of(args)
    if sym is None:
        raise ProxyKill(f"{tool}: no symbol in args — refusing")
    if sym not in UNIVERSE:
        raise ProxyKill(f"{tool}: {sym} not in allow-list — refusing")
    if str(args.get("side", "")).lower() in {"sell_short", "short"}:
        raise ProxyKill(f"{tool}: shorting not permitted")


# ---- low-level forward ----------------------------------------------------
def forward(tool: str, args: dict) -> dict:
    """Forward a single tool call upstream, with gates on order tools."""
    _assert_live_allowed()
    if tool in {ORDER_TOOL, REVIEW_TOOL, CANCEL_TOOL}:
        _validate_order(tool, args)            # reject BEFORE any network/credential
    return client.call_tool(tool, args)


# ---- read helpers (let the runner pull real state) ------------------------
def get_account_equity() -> float:
    """Total equity of the dedicated Agentic account."""
    accts = forward("get_accounts", {})
    # response shape TBD — handle a couple of plausible layouts defensively.
    if isinstance(accts, dict):
        for key in ("agentic_account", "account", "accounts"):
            node = accts.get(key)
            if isinstance(node, dict) and "equity" in node:
                return float(node["equity"])
        if "equity" in accts:
            return float(accts["equity"])
    raise client.MCPError(f"could not read equity from get_accounts result: {accts!r}")


def get_positions() -> dict[str, float]:
    """Current Agentic-account positions as {symbol: market_value_weight}."""
    pos = forward("get_equity_positions", {})
    rows = pos.get("positions", pos) if isinstance(pos, dict) else pos
    out: dict[str, float] = {}
    total = 0.0
    tmp = {}
    for r in (rows or []):
        sym = r.get("symbol") or r.get("ticker")
        mv = float(r.get("market_value", r.get("value", 0.0)))
        if sym:
            tmp[sym] = mv
            total += mv
    if total > 0:
        out = {s: v / total for s, v in tmp.items()}
    return out


# ---- the trade safety gate ------------------------------------------------
def _is_blocked(review: dict) -> str | None:
    """Return a reason string if the review result contains a blocking warning."""
    warnings = []
    for k in ("warnings", "alerts", "errors", "messages"):
        v = review.get(k) if isinstance(review, dict) else None
        if isinstance(v, list):
            warnings += [str(x).lower() for x in v]
        elif isinstance(v, str):
            warnings.append(v.lower())
    blob = " ".join(warnings) + " " + str(review).lower()
    hit = next((w for w in BLOCKING_WARNINGS if w in blob), None)
    return hit


def review_then_place(order: dict) -> dict:
    """Pre-trade gate: review_equity_order -> inspect warnings -> place_equity_order.

    `order` is the broker-ready arg dict (symbol/side/amount|quantity/type/...).
    Exact param names must be confirmed against RH's schema; this routes them
    through unchanged so confirming the schema is a one-place edit.
    """
    _validate_order(ORDER_TOOL, order)

    review = forward(REVIEW_TOOL, order)
    blocker = _is_blocked(review)
    if blocker:
        raise ProxyKill(f"review_equity_order flagged '{blocker}' — not placing {order.get('symbol')}")

    return forward(ORDER_TOOL, order)


if __name__ == "__main__":
    # Gate smoke test — no network. Confirms validation fires before any send.
    print("== order-tool gates ==")
    for tool, args, label in [
        (ORDER_TOOL, {"symbol": "XLK", "side": "buy", "amount": 100}, "allowed symbol"),
        (ORDER_TOOL, {"symbol": "TSLA", "side": "buy", "amount": 100}, "off-list symbol"),
        (ORDER_TOOL, {"symbol": "XLF", "side": "short", "amount": 100}, "short attempt"),
        (ORDER_TOOL, {"side": "buy", "amount": 100}, "missing symbol"),
    ]:
        try:
            forward(tool, args)
        except (ProxyKill, NotImplementedError, FileNotFoundError, client.MCPError) as e:
            print(f"  [{label:16}] {type(e).__name__}: {str(e).splitlines()[0]}")

    print("== read helpers ==")
    for fn, label in [(get_account_equity, "get_account_equity"), (get_positions, "get_positions")]:
        try:
            fn()
        except (ProxyKill, NotImplementedError, FileNotFoundError, client.MCPError) as e:
            print(f"  [{label:18}] {type(e).__name__}: {str(e).splitlines()[0]}")
