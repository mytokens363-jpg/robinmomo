"""
Risk rails. These are HARD gates, not suggestions. Every order must pass all of
them or it is rejected (and, for the daily-loss case, the kill switch latches).

The runner calls these; the proxy SHOULD also enforce the allow-list independently
so a bug in the runner can't place an off-list order. Defense in depth.
"""
from __future__ import annotations
import calendar, datetime as dt

from strategy.config import (
    UNIVERSE, PER_LEG_MAX_FRACTION, DAILY_LOSS_KILL_PCT, NO_TRADE_BAND, NO_MARGIN,
)


class RailBreach(Exception):
    """Raised for a breach that should HALT the run (e.g. daily-loss kill)."""


def is_rebalance_day(today: dt.date | None = None) -> bool:
    """True only on the last trading day of the month (Mon-Fri approximation;
    holidays are handled by the fact that the runner also checks last_rebalance
    so it won't double-fire). Frozen cadence — see config note."""
    today = today or dt.date.today()
    last_dom = calendar.monthrange(today.year, today.month)[1]
    # walk back from month end to the last weekday
    d = dt.date(today.year, today.month, last_dom)
    while d.weekday() >= 5:            # Sat=5, Sun=6
        d -= dt.timedelta(days=1)
    return today == d


def filter_no_trade_band(orders: list[dict]) -> list[dict]:
    """Drop legs whose absolute weight change is below the no-trade band."""
    return [o for o in orders if abs(o.get("weight_delta", 0.0)) >= NO_TRADE_BAND]


def check_allow_list(orders: list[dict]) -> None:
    for o in orders:
        if o["symbol"] not in UNIVERSE:
            raise RailBreach(f"allow-list breach: {o['symbol']} not in universe")


def check_no_short(orders: list[dict]) -> None:
    """Long-only. No order may drive a target weight negative."""
    if not NO_MARGIN:
        return
    for o in orders:
        if o.get("target_weight", 0.0) < -1e-9:
            raise RailBreach(f"short/negative weight rejected: {o['symbol']}")


def check_per_leg_cap(orders: list[dict]) -> list[dict]:
    """Reject any single leg whose notional exceeds the per-leg cap of equity.
    Returns the orders unchanged if all pass; raises on breach."""
    for o in orders:
        frac = abs(o.get("notional_fraction", 0.0))
        if frac > PER_LEG_MAX_FRACTION + 1e-9:
            raise RailBreach(
                f"per-leg cap breach: {o['symbol']} {frac:.1%} > {PER_LEG_MAX_FRACTION:.0%}")
    return orders


def check_daily_loss(state: dict, equity: float) -> None:
    """Latch the kill switch if intraday equity has fallen more than the
    daily-loss threshold below the day's high-water mark."""
    hw = state.get("day_high_equity")
    if hw and equity < hw * (1.0 - DAILY_LOSS_KILL_PCT):
        drop = 1.0 - equity / hw
        raise RailBreach(f"daily-loss kill: -{drop:.1%} from intraday high {hw:.2f}")


def assert_not_killed(state: dict) -> None:
    if state.get("kill_switch"):
        raise RailBreach(f"kill switch latched: {state.get('kill_reason')}")
