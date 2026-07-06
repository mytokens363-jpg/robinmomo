#!/bin/bash
# robinmomo month-end scheduler — cron entry point.
#
# Cron fires this daily on weekdays; the internal rails.is_rebalance_day check
# decides whether to actually do anything. On non-rebalance days (20+ per
# month) the wrapper exits silently with no output and no logfile touched.
# On the last NYSE trading day of the month it invokes the runner in DRY-RUN.
#
# This wrapper does NOT pass --live and does NOT set LIVE_TRADING. First cycle
# is dry-run only. Editing this to enable live is a deliberate decision.
#
# Path 1: robinmomo trades through the in-session Claude MCP. A cron-triggered
# subprocess canNOT authenticate to Robinhood on its own. If --live is later
# added, the runner's own liveness precondition will detect the missing
# in-session driver, fire a Telegram alert, and exit non-zero. That is the
# design; the scheduler intentionally offloads liveness handling to the runner.
#
# Cron (weekday 10:00 local; box is America/New_York so this is 10:00 ET):
#   0 10 * * 1-5  /home/rivet/projects/robinmomo-canonical/scripts/run_if_rebalance_day.sh
#
# systemd-timer equivalent (put in ~/.config/systemd/user/robinmomo.timer +
# ~/.config/systemd/user/robinmomo.service, then `systemctl --user enable
# --now robinmomo.timer`):
#   # robinmomo.timer
#   [Unit]
#   Description=robinmomo month-end scheduler
#   [Timer]
#   OnCalendar=Mon..Fri 10:00
#   Persistent=true
#   [Install]
#   WantedBy=timers.target
#
#   # robinmomo.service
#   [Unit]
#   Description=robinmomo month-end scheduler
#   [Service]
#   Type=oneshot
#   ExecStart=/home/rivet/projects/robinmomo-canonical/scripts/run_if_rebalance_day.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Activate venv (rails needs pandas_market_calendars; runner needs pandas/numpy/yfinance).
# shellcheck disable=SC1091
source "$REPO/.venv/bin/activate"

# Hydrate env from .env (cron has a minimal env by default).
if [ -f "$REPO/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO/.env"
  set +a
fi

# Check today via the frozen rails.is_rebalance_day. Silent exit on the ~20
# non-rebalance days each month. NO --force-rebalance here — the real cadence
# guard governs.
python3 -c 'from live import rails; import sys; sys.exit(0 if rails.is_rebalance_day() else 1)' \
  || exit 0

# Rebalance day. Invoke the runner in DRY-RUN and log everything.
# The runner internally fires notify.rebalance + notify.heartbeat on success;
# if --live is ever added the liveness precondition fires notify.halt on
# session death and exits non-zero here.
mkdir -p "$REPO/logs"
LOGFILE="$REPO/logs/scheduler_$(date +%Y-%m-%d).log"
{
  echo ""
  echo "===== scheduler fired $(date -Iseconds) — invoking runner (dry-run) ====="
  python3 -m live.runner --source yfinance
} >> "$LOGFILE" 2>&1
