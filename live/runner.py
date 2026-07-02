#!/usr/bin/env python3
"""
robinmomo live runner — DETERMINISTIC. The LLM is not in this loop.

Flow each invocation:
  1. load price history (yfinance live, or synthetic for offline test)
  2. roll the intraday high-water mark; enforce kill switch + daily-loss rail
  3. if today is NOT the frozen month-end rebalance day -> monitor only, exit
  4. compute target weights (validated signal logic)
  5. diff vs last-known positions -> order legs
  6. apply rails: no-trade band, allow-list, no-short, per-leg cap
  7. execute_orders():
        DRY-RUN  -> log intended orders, touch nothing
        LIVE     -> hand the order list to the MCP proxy (requires LIVE_TRADING=true
                    AND kill switch clear AND all rails passed)

Run:
  Offline logic test:  python3 -m live.runner --source synthetic
  Live data, dry-run:  python3 -m live.runner --source yfinance
  Arm live (later):    LIVE_TRADING=true python3 -m live.runner --source yfinance --live

Nothing is sent to a broker until BOTH --live is passed AND env LIVE_TRADING=true.
Default is always dry-run.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys

from strategy.config import UNIVERSE, DRY_RUN_DEFAULT
from strategy.signal import compute_target_weights
from strategy import data
from live import state as st
from live import rails
from live import notify
from proxy import mcp_proxy


def _log(msg: str) -> None:
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}")


def build_orders(target: dict[str, float], current: dict[str, float]) -> list[dict]:
    """Diff target vs current weights into order legs. Notional fractions are in
    units of account equity (target 0.25 = 25% of equity)."""
    syms = set(target) | set(current)
    orders = []
    for s in sorted(syms):
        tw = target.get(s, 0.0)
        cw = current.get(s, 0.0)
        delta = tw - cw
        if abs(delta) < 1e-9:
            continue
        orders.append({
            "symbol": s,
            "side": "buy" if delta > 0 else "sell",
            "target_weight": tw,
            "weight_delta": delta,
            "notional_fraction": delta,   # signed fraction of equity to trade
        })
    return orders


def execute_orders(orders: list[dict], *, dry_run: bool, account_equity: float) -> None:
    """STUB execution boundary. In dry-run, logs intended orders only.

    LIVE path is intentionally unimplemented: it must route through proxy/mcp_proxy.py,
    which holds the Robinhood OAuth token and re-enforces the allow-list. Wiring it
    requires an open, authenticated Agentic Trading account (does not exist yet).
    """
    if not orders:
        _log("no orders after rails — portfolio already at target")
        return

    _log(f"intended orders ({len(orders)}):")
    for o in orders:
        notional = o["notional_fraction"] * account_equity
        print(f"    {o['side'].upper():4}  {o['symbol']:5}  "
              f"target_w={o['target_weight']:.2f}  Δw={o['weight_delta']:+.2f}  "
              f"≈ ${notional:+,.0f}")

    if dry_run:
        _log("DRY-RUN: nothing sent. (set --live AND LIVE_TRADING=true to arm)")
        return

    # --- LIVE PATH ----------------------------------------------------------
    if os.environ.get("LIVE_TRADING") != "true":
        raise SystemExit("refusing to trade: LIVE_TRADING != true")
    for o in orders:
        amount = abs(o["notional_fraction"]) * account_equity
        broker_order = {
            "symbol": o["symbol"],
            "side": o["side"],
            "amount": round(amount, 2),     # dollar-based; confirm param name vs RH schema
            "type": "market",
        }
        result = mcp_proxy.review_then_place(broker_order)
        _log(f"placed {o['side']} {o['symbol']} ~${amount:,.0f} -> {result}")


def run(source: str, seed: int, live: bool) -> None:
    dry_run = DRY_RUN_DEFAULT or (not live)

    # 1. data
    if source == "yfinance":
        daily = data.load_yfinance()
    else:
        daily = data.load_synthetic(seed=seed)

    # equity + positions: live mode reads them from the broker via the proxy;
    # dry-run uses a nominal equity and last-known positions from state.
    state = st.load()
    if live:
        account_equity = mcp_proxy.get_account_equity()
        live_positions = mcp_proxy.get_positions()
        state["positions"] = {s: {"weight": w, "qty": None} for s, w in live_positions.items()}
    else:
        account_equity = float(os.environ.get("ROBINMOMO_NOMINAL_EQUITY", "10000"))

    # 2. daily-loss / kill rails (run EVERY invocation, not just rebalance days)
    state = st.roll_day(state, account_equity)
    try:
        rails.assert_not_killed(state)
        rails.check_daily_loss(state, account_equity)
    except rails.RailBreach as e:
        state = st.latch_kill(state, str(e))
        _log(f"HALT — {e}")
        notify.halt(str(e))
        st.save(state)
        return

    # 3. cadence guard
    today = dt.date.today()
    if not rails.is_rebalance_day(today):
        _log(f"not a rebalance day ({today}); monitor-only. last_rebalance="
             f"{state.get('last_rebalance')}")
        if os.environ.get("ROBINMOMO_HEARTBEAT") == "1":
            notify.heartbeat(today.isoformat(), state.get("last_rebalance"))
        st.save(state)
        return
    if state.get("last_rebalance") == today.isoformat():
        _log("already rebalanced today; skipping")
        st.save(state)
        return

    # 4. signal
    try:
        target = compute_target_weights(daily)
    except ValueError as e:
        _log(f"no action — {e}")
        st.save(state)
        return
    _log("target weights: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(target.items())))

    # 5. diff
    current = {k: v.get("weight", 0.0) for k, v in state.get("positions", {}).items()}
    orders = build_orders(target, current)

    # 6. rails
    try:
        orders = rails.filter_no_trade_band(orders)
        rails.check_allow_list(orders)
        rails.check_no_short(orders)
        rails.check_per_leg_cap(orders)
    except rails.RailBreach as e:
        state = st.latch_kill(state, str(e))
        _log(f"HALT — rail breach: {e}")
        notify.halt(f"rail breach: {e}")
        st.save(state)
        return

    # 7. execute (stubbed)
    execute_orders(orders, dry_run=dry_run, account_equity=account_equity)
    notify.rebalance(target, orders, account_equity)

    # record intent in state (dry-run still records target so diffs are realistic)
    state["positions"] = {s: {"weight": w, "qty": None} for s, w in target.items()}
    state["last_rebalance"] = today.isoformat()
    st.save(state)
    _log("state updated.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["yfinance", "synthetic"], default="synthetic")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--live", action="store_true",
                    help="attempt live execution (also requires env LIVE_TRADING=true)")
    ap.add_argument("--force-rebalance", action="store_true",
                    help="TESTING ONLY: bypass the month-end cadence guard")
    args = ap.parse_args()

    if args.force_rebalance:
        rails.is_rebalance_day = lambda *a, **k: True  # noqa: test shim
        _log("WARNING: cadence guard bypassed (--force-rebalance)")

    run(args.source, args.seed, args.live)


if __name__ == "__main__":
    main()
