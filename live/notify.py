"""
Telegram alerts for the robinmomo runner.

Thin push channel — silent no-op when TELEGRAM_BOT_TOKEN is absent so a dry-run
on a box without secrets stays clean and just doesn't ping. Failures are
warned-and-swallowed; observability MUST NOT take down the strategy loop.

Env:
    TELEGRAM_BOT_TOKEN   bot token from BotFather. Missing => no-op.
    TELEGRAM_CHAT_ID     chat id to push to. Defaults to the shared monitor chat.

The three helpers (rebalance / halt / heartbeat) build a plain-text message and
hand it to send(). Each helper wraps its own body so a builder bug (KeyError,
formatting crash) can't propagate into the runner.
"""
from __future__ import annotations
import json, os, sys
import urllib.error, urllib.request

API = "https://api.telegram.org/bot{token}/sendMessage"
DEFAULT_CHAT_ID = "1054649761"  # shared monitor chat (BTC / salvage / robinmomo)
TIMEOUT_S = 5.0


def _config() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    return token, chat_id


def send(text: str) -> None:
    """Push a plain-text message. No-op without token. Never raises."""
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            API.format(token=token),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            r.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        print(f"[notify] warn: telegram send failed: {e}", file=sys.stderr)


def rebalance(targets: dict[str, float], orders: list[dict], equity: float) -> None:
    try:
        lines = ["[robinmomo] REBALANCE FIRED"]
        lines.append("targets: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(targets.items())))
        if orders:
            lines.append(f"orders ({len(orders)}):")
            for o in orders:
                notional = o.get("notional_fraction", 0.0) * equity
                lines.append(
                    f"  {str(o.get('side','?')).upper()} {o.get('symbol','?')}  "
                    f"Δw={o.get('weight_delta', 0.0):+.0%}  ≈ ${notional:+,.0f}"
                )
        else:
            lines.append("no orders after rails — already at target")
        send("\n".join(lines))
    except Exception as e:
        print(f"[notify] warn: rebalance message build failed: {e}", file=sys.stderr)


def halt(reason: str) -> None:
    try:
        send(f"[robinmomo] HALT — kill switch latched\n{reason}")
    except Exception as e:
        print(f"[notify] warn: halt message build failed: {e}", file=sys.stderr)


def heartbeat(today, last_rebalance) -> None:
    try:
        send(f"[robinmomo] heartbeat {today} — monitor-only; last_rebalance={last_rebalance}")
    except Exception as e:
        print(f"[notify] warn: heartbeat message build failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    # Self-smoke: stub send, exercise each builder, print what would have been pushed.
    captured: list[str] = []
    globals()["send"] = captured.append
    rebalance(
        {"XLK": 0.25, "XLB": 0.25, "XLE": 0.25, "XLI": 0.25},
        [
            {"symbol": "XLK", "side": "buy", "target_weight": 0.25,
             "weight_delta": 0.25, "notional_fraction": 0.25},
            {"symbol": "XLB", "side": "buy", "target_weight": 0.25,
             "weight_delta": 0.25, "notional_fraction": 0.25},
        ],
        10000.0,
    )
    halt("daily-loss kill: -8.5% from intraday high 10234.12")
    heartbeat("2026-06-17", "2026-05-29")
    for msg in captured:
        print("--- would push ---")
        print(msg)
