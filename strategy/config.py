"""
Frozen strategy configuration for robinmomo.

These are the EXACT parameters validated by the backtest + robustness sweep:
dual-momentum sector rotation, TOP_N=4, 12m absolute filter, MONTH-END rebalance.

The sweep showed (TOP_N, ABS_FILTER_LB_M) sits on a flat plateau (robust), but the
rebalance DATE is sensitive — mid-month cost ~10pts of drawdown protection. So the
rebalance day is frozen here and the runner enforces it. Do not "drift" it.

Changing anything in this file changes the proven strategy. Treat edits as a
deliberate, version-controlled decision, not a tweak.
"""
from __future__ import annotations

# --- universe -------------------------------------------------------------
SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]
CASH = "BIL"                       # T-bill ETF, the absolute-momentum benchmark + parking slot
UNIVERSE = SECTORS + [CASH]        # the ONLY symbols any order may reference (allow-list)

# --- signal (validated) ---------------------------------------------------
TOP_N = 4                          # hold top 4 by blended momentum
LOOKBACKS_M = [3, 6, 12]           # blended momentum windows, months
ABS_FILTER_LB_M = 12               # absolute-momentum lookback vs BIL (the crash breaker)

# --- cadence (frozen — see note above) ------------------------------------
REBALANCE = "month_end"            # rebalance on the last trading day of the month
EQUAL_WEIGHT = 1.0 / TOP_N         # 25% per slot

# --- risk rails -----------------------------------------------------------
# These are HARD limits enforced by live/rails.py. They are not strategy knobs;
# they are the "don't blow the account" boundary. Tune to your funded size.
NO_MARGIN = True                   # long-only, cash behavior. never relax.
PER_LEG_MAX_FRACTION = 0.30        # reject any single order > 30% of account equity
                                   # (target is 25%; 30% leaves drift headroom, not abuse room)
DAILY_LOSS_KILL_PCT = 0.08         # if intraday equity drops >8% from day's high-water mark,
                                   # halt all orders, latch kill switch, require manual re-arm
NO_TRADE_BAND = 0.03               # skip any rebalance leg smaller than 3% weight drift
MIN_HISTORY_MONTHS = 13            # need >12m history before any signal is valid

# --- execution safety -----------------------------------------------------
# execute_orders() refuses to place anything unless ALL are true:
#   - env LIVE_TRADING == "true"   (explicit opt-in, default off)
#   - kill switch not latched
#   - every order passes rails
# Default posture is DRY-RUN: intended orders are logged, nothing is sent.
DRY_RUN_DEFAULT = True
