"""
Signal computation — identical logic to the validated backtest, isolated so the
live runner and the backtest can never drift apart.

compute_target_weights(daily_closes) -> {symbol: weight} summing to 1.0
"""
from __future__ import annotations
import pandas as pd

from strategy.config import (
    SECTORS, CASH, TOP_N, LOOKBACKS_M, ABS_FILTER_LB_M, EQUAL_WEIGHT, MIN_HISTORY_MONTHS,
)


def _month_end(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("ME").last()


def _momentum_scores(me: pd.DataFrame) -> pd.Series:
    """Blended 3/6/12-month total return as of the latest month-end row."""
    cur = me.iloc[-1]
    parts = [cur / me.iloc[-1 - lb] - 1.0 for lb in LOOKBACKS_M]
    return pd.concat(parts, axis=1).mean(axis=1)


def compute_target_weights(daily: pd.DataFrame) -> dict[str, float]:
    """Return target portfolio weights for the CURRENT month-end signal.

    Raises ValueError if there isn't enough history to form a valid signal —
    the runner treats that as 'do nothing', never as 'guess'.
    """
    me = _month_end(daily.dropna(how="all").ffill())
    if len(me) < MIN_HISTORY_MONTHS:
        raise ValueError(f"insufficient history: {len(me)} months < {MIN_HISTORY_MONTHS}")

    scores = _momentum_scores(me).reindex(SECTORS).dropna()
    if scores.empty:
        raise ValueError("no scorable sectors")

    picks = list(scores.sort_values(ascending=False).index[:TOP_N])

    # absolute-momentum filter: any pick weaker than BIL over 12m forfeits to cash
    bil = me[CASH]
    bil_mom = bil.iloc[-1] / bil.iloc[-1 - ABS_FILTER_LB_M] - 1.0
    kept = []
    for s in picks:
        s_px = me[s].dropna()
        if len(s_px) > ABS_FILTER_LB_M:
            s_mom = s_px.iloc[-1] / s_px.iloc[-1 - ABS_FILTER_LB_M] - 1.0
            kept.append(s if s_mom > bil_mom else CASH)
        else:
            kept.append(CASH)

    weights: dict[str, float] = {}
    for sym in kept:
        weights[sym] = weights.get(sym, 0.0) + EQUAL_WEIGHT
    return weights
