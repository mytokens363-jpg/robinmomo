"""
Persistent state for the live runner. Plain JSON on disk so it's inspectable and
restart-safe. No daemon; each run reads state, acts, writes state.

State lives at $ROBINMOMO_STATE or ./state/robinmomo_state.json (gitignored).
"""
from __future__ import annotations
import json, os, datetime as dt
from pathlib import Path

_DEFAULT = {
    "kill_switch": False,            # latched true on a rail breach; manual re-arm only
    "kill_reason": None,
    "day": None,                     # YYYY-MM-DD of the current high-water tracking day
    "day_high_equity": None,         # intraday high-water mark for daily-loss kill
    "last_rebalance": None,          # YYYY-MM-DD of last executed/dry-run rebalance
    "positions": {},                 # {symbol: {"qty": float, "weight": float}} last known
}


def _path() -> Path:
    p = Path(os.environ.get("ROBINMOMO_STATE", "state/robinmomo_state.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    p = _path()
    if not p.exists():
        return dict(_DEFAULT)
    s = dict(_DEFAULT)
    s.update(json.loads(p.read_text()))
    return s


def save(state: dict) -> None:
    _path().write_text(json.dumps(state, indent=2, sort_keys=True))


def latch_kill(state: dict, reason: str) -> dict:
    state["kill_switch"] = True
    state["kill_reason"] = reason
    save(state)
    return state


def rearm(state: dict) -> dict:
    """Manual, deliberate re-arm. Intentionally not callable from the runner loop."""
    state["kill_switch"] = False
    state["kill_reason"] = None
    save(state)
    return state


def roll_day(state: dict, equity: float | None) -> dict:
    """Reset the intraday high-water mark when the calendar day changes."""
    today = dt.date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["day_high_equity"] = equity
    elif equity is not None:
        prev = state.get("day_high_equity")
        state["day_high_equity"] = equity if prev is None else max(prev, equity)
    return state
