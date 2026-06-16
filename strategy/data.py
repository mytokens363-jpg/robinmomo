"""
Price data access. Two sources:
  - yfinance : real daily closes, for live use on the GX10 (needs Yahoo egress)
  - synthetic: deterministic GBM, for offline logic testing where Yahoo is firewalled

Both return a DataFrame of daily closes indexed by date, columns = UNIVERSE.
The runner only needs trailing history (>13 months) to compute the current signal.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

from strategy.config import UNIVERSE, SECTORS, CASH


def load_yfinance(start: str = "2022-01-01", end: str | None = None) -> pd.DataFrame:
    """Trailing daily closes for the universe. Default start gives ample lookback
    for a 12m signal; widen if you want more cushion."""
    import yfinance as yf
    df = yf.download(UNIVERSE, start=start, end=end, auto_adjust=True, progress=False)["Close"]
    df = df.dropna(how="all").ffill()
    present = [t for t in UNIVERSE if t in df.columns and not df[t].isna().all()]
    missing = [t for t in UNIVERSE if t not in present]
    if missing:
        print(f"[data] note: no data for {missing} (late-listing sectors are normal)", file=sys.stderr)
    return df


def load_synthetic(seed: int = 11, days: int = 252 * 3) -> pd.DataFrame:
    """Deterministic fake prices so the runner's logic can be exercised offline.
    NOT a backtest — just enough realistic shape to produce a valid signal + diff."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    n = len(UNIVERSE)
    mkt = rng.normal(0.0004, 0.010, days)
    betas = rng.uniform(0.7, 1.3, n); betas[-1] = 0.02
    drift = rng.uniform(-0.0001, 0.0006, n); drift[-1] = 0.00008
    idio = rng.uniform(0.006, 0.012, n); idio[-1] = 0.0004
    rets = drift[None, :] + betas[None, :] * mkt[:, None] + rng.normal(0, 1, (days, n)) * idio[None, :]
    prices = 100 * np.exp(np.cumsum(rets, axis=0))
    return pd.DataFrame(prices, index=dates, columns=UNIVERSE)
