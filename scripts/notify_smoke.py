#!/usr/bin/env python3
"""
Smoke test the Telegram notify hooks in the runner.

Replaces notify.send with a capture so we see the *would-be* pushes without
hitting the network. Exercises all three hook points:
    1. REBALANCE FIRED   — force a rebalance on clean state.
    2. HALT              — pre-latch the kill switch, runner aborts on the
                           assert_not_killed rail, halt() fires.
    3. HEARTBEAT         — monitor-only day with ROBINMOMO_HEARTBEAT=1.

Run from project root:  python3 scripts/notify_smoke.py
"""
from __future__ import annotations
import json, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from live import notify, rails, runner


def reset_state(extra: dict | None = None) -> None:
    state_path = ROOT / "state" / "robinmomo_state.json"
    state_path.parent.mkdir(exist_ok=True)
    base = {
        "kill_switch": False, "kill_reason": None,
        "day": None, "day_high_equity": None,
        "last_rebalance": None, "positions": {},
    }
    if extra:
        base.update(extra)
    state_path.write_text(json.dumps(base, indent=2, sort_keys=True))


def run_scenario(label: str, body) -> list[str]:
    print(f"\n=== {label} ===")
    captured: list[str] = []
    saved_send = notify.send
    notify.send = captured.append
    try:
        body()
    finally:
        notify.send = saved_send
    if not captured:
        print("  (no notifications pushed)")
    else:
        for msg in captured:
            for line in msg.splitlines():
                print(f"  | {line}")
    return captured


def main() -> None:
    saved_is_rb = rails.is_rebalance_day

    # 1. REBALANCE FIRED
    def _rebalance():
        reset_state()
        rails.is_rebalance_day = lambda *a, **k: True
        runner.run(source="yfinance", seed=11, live=False)
    run_scenario("REBALANCE FIRED", _rebalance)
    rails.is_rebalance_day = saved_is_rb

    # 2. HALT — latched kill switch
    def _halt():
        reset_state({"kill_switch": True, "kill_reason": "smoke-test pre-latch"})
        runner.run(source="yfinance", seed=11, live=False)
    run_scenario("HALT — latched kill", _halt)

    # 3. HEARTBEAT — monitor-only with toggle
    def _heartbeat():
        reset_state()
        os.environ["ROBINMOMO_HEARTBEAT"] = "1"
        try:
            runner.run(source="yfinance", seed=11, live=False)
        finally:
            os.environ.pop("ROBINMOMO_HEARTBEAT", None)
    run_scenario("HEARTBEAT (monitor-only)", _heartbeat)


if __name__ == "__main__":
    main()
