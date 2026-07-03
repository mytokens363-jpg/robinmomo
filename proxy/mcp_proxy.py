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
def get_account_equity(account_number: str) -> float:
    """Total portfolio value the strategy sizes weights against.

    Uses get_portfolio.total_value (the sum-of-asset-class + cash figure the
    broker itself reports). Falls back to equity_value + cash only if
    total_value is absent — defensive; the schema documents total_value as
    always present."""
    p = forward("get_portfolio", {"account_number": account_number})
    data = p.get("data", p) if isinstance(p, dict) else None
    if not isinstance(data, dict):
        raise client.MCPError(f"unexpected get_portfolio result: {p!r}")
    if "total_value" in data:
        return float(data["total_value"])
    if "equity_value" in data or "cash" in data:
        return float(data.get("equity_value", 0)) + float(data.get("cash", 0))
    raise client.MCPError(f"could not read equity from get_portfolio result: {p!r}")


def get_positions(account_number: str) -> dict[str, float]:
    """Current positions as {symbol: weight}, weight = market_value / total.

    get_equity_positions returns quantity but no market value; multiply by the
    live last_trade_price from get_equity_quotes and divide by portfolio total.
    Empty account -> {}."""
    pos = forward("get_equity_positions", {"account_number": account_number})
    rows: list = []
    if isinstance(pos, dict):
        data = pos.get("data", pos)
        if isinstance(data, dict):
            rows = data.get("positions", []) or []
    qty: dict[str, float] = {}
    for r in rows:
        sym = r.get("symbol") or r.get("ticker")
        q = float(r.get("quantity", 0.0))
        if sym and q != 0:
            qty[sym] = q
    if not qty:
        return {}

    quotes = forward("get_equity_quotes", {"symbols": list(qty)})
    prices: dict[str, float] = {}
    if isinstance(quotes, dict):
        qdata = quotes.get("data", quotes)
        if isinstance(qdata, dict):
            for row in qdata.get("results", []) or []:
                q = row.get("quote") or {}
                sym = q.get("symbol")
                px = q.get("last_trade_price")
                if sym and px is not None:
                    prices[sym] = float(px)

    total = get_account_equity(account_number)
    if total <= 0:
        raise client.MCPError(f"total portfolio value is {total}; cannot compute weights")

    out: dict[str, float] = {}
    for sym, q in qty.items():
        px = prices.get(sym)
        if px is None:
            raise client.MCPError(f"no last_trade_price for {sym}")
        out[sym] = (q * px) / total
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
