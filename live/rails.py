"""
Risk rails. These are HARD gates, not suggestions. Every order must pass all of
them or it is rejected (and, for the daily-loss case, the kill switch latches).

The runner calls these; the proxy SHOULD also enforce the allow-list independently
so a bug in the runner can't place an off-list order. Defense in depth.
"""
from __future__ import annotations
import calendar, datetime as dt, sys

from strategy.config import (
    UNIVERSE, PER_LEG_MAX_FRACTION, DAILY_LOSS_KILL_PCT, NO_TRADE_BAND, NO_MARGIN,
)


class RailBreach(Exception):
    """Raised for a breach that should HALT the run (e.g. daily-loss kill)."""


_CAL_WARNED = False


def _last_trading_day_of_month(year: int, month: int) -> dt.date:
    """Last NYSE trading day of the month. Uses the real exchange calendar
    (holidays + early closes) when pandas_market_calendars is available; falls
    back to a weekday walk with a LOUD warning otherwise — a silent fallback
    would re-introduce exactly the holiday bug this function exists to kill."""
    global _CAL_WARNED
    last_dom = calendar.monthrange(year, month)[1]
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("XNYS")
        days = nyse.valid_days(start_date=f"{year}-{month:02d}-01",
                               end_date=f"{year}-{month:02d}-{last_dom:02d}")
        if len(days):
            return days[-1].date()
        # no trading days in range should be impossible; fall through to walk
    except Exception as e:
        if not _CAL_WARNED:
            print(f"[rails] WARNING: NYSE calendar unavailable ({e}); falling back to "
                  f"weekday approximation — holiday-adjacent month-ends may misfire. "
                  f"Install pandas_market_calendars to fix.", file=sys.stderr)
            _CAL_WARNED = True
    # fallback: walk back from month end to the last weekday
    d = dt.date(year, month, last_dom)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def is_rebalance_day(today: dt.date | None = None) -> bool:
    """True only on the last NYSE trading day of the month. Frozen cadence —
    the sweep showed the rebalance date is sensitive, so this is enforced.
    The runner also checks last_rebalance, so it can never double-fire."""
    today = today or dt.date.today()
    return today == _last_trading_day_of_month(today.year, today.month)


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
