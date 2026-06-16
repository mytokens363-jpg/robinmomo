#!/usr/bin/env python3
"""
Dual-momentum sector rotation — BACKTEST ONLY. No broker, no MCP, no live orders.

Strategy under test
-------------------
Universe : 11 GICS sector SPDRs + BIL (T-bill cash proxy)
Signal   : per-sector momentum = mean(3m, 6m, 12m total return), computed monthly
Select   : top 4 by score
Filter   : absolute momentum — any selected sector whose own 12m return is below
           BIL's 12m return forfeits its slot to BIL (this is the crash circuit breaker)
Weight   : equal-weight, 25% per slot (cash-filtered slots become BIL)
Cadence  : monthly; signal on month-end close, EXECUTED next session open (T+1, no lookahead)
Frictions: per-leg slippage in bps applied to turnover

The go/no-go number is max drawdown FILTER-ON vs FILTER-OFF. If the filter doesn't
meaningfully cut drawdown, the core thesis is wrong — stop before building anything live.

Usage
-----
Real data (run on GX10, egress open):   python3 momo_backtest.py --source yfinance --start 2007-01-01
Synthetic dry-run (logic proof):        python3 momo_backtest.py --source synthetic --seed 7
"""
from __future__ import annotations
import argparse, sys
import numpy as np
import pandas as pd

SECTORS = ["XLK","XLF","XLV","XLY","XLP","XLE","XLI","XLB","XLU","XLRE","XLC"]
CASH = "BIL"
TOP_N = 4
LOOKBACKS_M = [3, 6, 12]   # months
ABS_FILTER_LB_M = 12       # absolute-momentum lookback
SLIPPAGE_BPS = 5.0         # per unit turnover, one-way


# ----------------------------- data -----------------------------
def load_yfinance(start: str, end: str | None) -> pd.DataFrame:
    import yfinance as yf
    tickers = SECTORS + [CASH]
    df = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)["Close"]
    df = df.dropna(how="all").ffill()
    missing = [t for t in tickers if t not in df.columns or df[t].isna().all()]
    if missing:
        # XLRE (2015) and XLC (2018) list late — that's expected; only abort if a core name is fully absent
        print(f"[warn] no data for: {missing} (late-listing sectors are normal)", file=sys.stderr)
    return df


def make_synthetic(seed: int = 7, days: int = 252 * 18) -> pd.DataFrame:
    """Geometric brownian motion with per-sector drift + a shared market factor and two
    engineered crashes, so the absolute filter has something real to react to."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2007-01-01", periods=days)
    tickers = SECTORS + [CASH]
    n = len(tickers)
    # market factor with two crash windows
    mkt = rng.normal(0.0003, 0.011, days)
    for lo, hi, mu, sig in [(260, 380, -0.004, 0.030), (3750, 3820, -0.006, 0.035)]:
        mkt[lo:hi] = rng.normal(mu, sig, hi - lo)
    betas = rng.uniform(0.7, 1.3, n); betas[-1] = 0.02            # BIL ~ market-neutral
    drift = rng.uniform(-0.0001, 0.0006, n); drift[-1] = 0.00008  # BIL tiny positive
    idio = rng.uniform(0.006, 0.013, n); idio[-1] = 0.0005
    rets = drift[None, :] + betas[None, :] * mkt[:, None] + rng.normal(0, 1, (days, n)) * idio[None, :]
    prices = 100 * np.exp(np.cumsum(rets, axis=0))
    df = pd.DataFrame(prices, index=dates, columns=tickers)
    # emulate late listings
    df.loc[: df.index[400], "XLRE"] = np.nan
    df.loc[: df.index[900], "XLC"] = np.nan
    return df


# ----------------------------- engine -----------------------------
def month_end_prices(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("ME").last()


def next_open_proxy(daily: pd.DataFrame, me: pd.DataFrame) -> pd.DataFrame:
    """T+1 execution: for each month-end signal date, use the next available daily close
    as the fill price (close-to-close proxy; we don't have intraday opens). Guarantees no
    same-bar lookahead."""
    fills = {}
    didx = daily.index
    for d in me.index:
        loc = didx.searchsorted(d, side="right")
        fills[d] = daily.iloc[loc] if loc < len(didx) else daily.iloc[-1]
    return pd.DataFrame(fills).T


def momentum_scores(me: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    px = me.loc[:asof]
    if len(px) <= max(LOOKBACKS_M):
        return pd.Series(dtype=float)
    cur = px.iloc[-1]
    parts = []
    for lb in LOOKBACKS_M:
        prev = px.iloc[-1 - lb]
        parts.append(cur / prev - 1.0)
    return pd.concat(parts, axis=1).mean(axis=1)


def run(daily: pd.DataFrame, use_filter: bool):
    me = month_end_prices(daily)
    fills = next_open_proxy(daily, me)
    dates = me.index[me.index >= me.index[max(LOOKBACKS_M)]]

    equity = 1.0
    curve, weights_log = [], []
    prev_w = pd.Series(0.0, index=daily.columns)

    for i, asof in enumerate(dates[:-1]):
        scores = momentum_scores(me, asof)
        scores = scores.reindex(SECTORS).dropna()
        if scores.empty:
            continue
        ranked = scores.sort_values(ascending=False)
        picks = list(ranked.index[:TOP_N])

        # absolute-momentum filter vs BIL's own 12m return
        if use_filter and CASH in me.columns:
            bil = me[CASH].loc[:asof]
            if len(bil) > ABS_FILTER_LB_M:
                bil_mom = bil.iloc[-1] / bil.iloc[-1 - ABS_FILTER_LB_M] - 1.0
                kept = []
                for s in picks:
                    s_px = me[s].loc[:asof].dropna()
                    if len(s_px) > ABS_FILTER_LB_M:
                        s_mom = s_px.iloc[-1] / s_px.iloc[-1 - ABS_FILTER_LB_M] - 1.0
                        kept.append(s if s_mom > bil_mom else CASH)
                    else:
                        kept.append(CASH)
                picks = kept

        tgt = pd.Series(0.0, index=daily.columns)
        for p in picks:
            tgt[p] += 1.0 / TOP_N

        # cost on turnover, then hold one month (fill-to-fill)
        turnover = (tgt - prev_w).abs().sum()
        cost = turnover * (SLIPPAGE_BPS / 1e4)
        nxt = dates[i + 1]
        p0, p1 = fills.loc[asof], fills.loc[nxt]
        period_ret = float((tgt * (p1 / p0 - 1.0)).sum())
        equity *= (1.0 - cost) * (1.0 + period_ret)

        curve.append((nxt, equity))
        weights_log.append((asof, turnover, {k: round(v, 3) for k, v in tgt[tgt > 0].items()}))
        prev_w = tgt

    eq = pd.Series(dict(curve)).sort_index()
    return eq, weights_log


# ----------------------------- metrics -----------------------------
def metrics(eq: pd.Series) -> dict:
    if len(eq) < 2:
        return {}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    dd = (eq / eq.cummax() - 1.0)
    mret = eq.pct_change().dropna()
    sharpe = (mret.mean() / mret.std() * np.sqrt(12)) if mret.std() > 0 else float("nan")
    return {"CAGR": cagr, "MaxDD": dd.min(), "Sharpe": sharpe,
            "Final": eq.iloc[-1], "Months": len(eq)}


def fmt(m: dict) -> str:
    if not m: return "  (insufficient history)"
    return (f"  CAGR {m['CAGR']*100:6.2f}%   MaxDD {m['MaxDD']*100:7.2f}%   "
            f"Sharpe {m['Sharpe']:4.2f}   Final {m['Final']:.2f}x   ({m['Months']} mo)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["yfinance", "synthetic"], default="synthetic")
    ap.add_argument("--start", default="2007-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="momo_results")
    args = ap.parse_args()

    if args.source == "yfinance":
        daily = load_yfinance(args.start, args.end)
    else:
        daily = make_synthetic(seed=args.seed)
    daily = daily.dropna(how="all").ffill()

    eq_on, log_on = run(daily, use_filter=True)
    eq_off, _ = run(daily, use_filter=False)
    m_on, m_off = metrics(eq_on), metrics(eq_off)

    print(f"\nData source: {args.source}   span: {daily.index[0].date()} -> {daily.index[-1].date()}")
    print("=" * 64)
    print("FILTER ON  (dual momentum, crash breaker active)"); print(fmt(m_on))
    print("FILTER OFF (relative momentum only)");               print(fmt(m_off))
    print("=" * 64)
    if m_on and m_off:
        # drawdowns are negative; ON shallower than OFF => positive improvement
        delta = (m_on["MaxDD"] - m_off["MaxDD"]) * 100
        print(f"GO/NO-GO  ->  drawdown cut by the filter: {delta:5.2f} pts")
        print(f"            (ON {m_on['MaxDD']*100:.1f}%  vs  OFF {m_off['MaxDD']*100:.1f}%)")
        verdict = "PASS — filter earns its place" if delta > 5 else "WEAK — rethink before building live"
        print(f"            verdict: {verdict}")

    # outputs
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5.5))
        ax.plot(eq_on.index, eq_on.values, label="Filter ON (dual momentum)", lw=1.7)
        ax.plot(eq_off.index, eq_off.values, label="Filter OFF (relative only)", lw=1.2, alpha=0.8)
        ax.set_yscale("log"); ax.set_title("Sector momentum rotation — growth of $1 (log)")
        ax.set_ylabel("equity (log)"); ax.legend(); ax.grid(True, which="both", alpha=0.25)
        fig.tight_layout(); fig.savefig(f"{args.out}_equity.png", dpi=130)
        print(f"\n[ok] equity curve -> {args.out}_equity.png")
    except Exception as e:
        print(f"[warn] plot skipped: {e}", file=sys.stderr)

    eq_on.to_frame("equity_filter_on").join(eq_off.to_frame("equity_filter_off"), how="outer")\
        .to_csv(f"{args.out}_curves.csv")
    pd.DataFrame([(d, t, str(w)) for d, t, w in log_on],
                 columns=["signal_date", "turnover", "target_weights"])\
        .to_csv(f"{args.out}_trades.csv", index=False)
    print(f"[ok] curves -> {args.out}_curves.csv")
    print(f"[ok] monthly targets/turnover -> {args.out}_trades.csv")


if __name__ == "__main__":
    main()
