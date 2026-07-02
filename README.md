# robinmomo

Deterministic dual-momentum sector rotation for a Robinhood Agentic Trading account.
The strategy logic is fixed Python; the LLM is **not** in the trade loop. Robinhood's
MCP is used purely as an execution surface, reached through a local OAuth-holding proxy.

> Status: strategy validated by backtest + robustness sweep. Runner + proxy are built;
> live execution is intentionally **stubbed** and cannot fire until an Agentic Trading
> account exists and the proxy is wired. Default posture is dry-run.

## Layout

```
strategy/   config.py   frozen, validated params + rail limits
            data.py     yfinance (live) / synthetic (offline test) price loader
            signal.py   target-weight logic, identical to the backtest
live/       runner.py   deterministic loop: signal -> diff -> rails -> execute(stub)
            rails.py    hard gates: allow-list, per-leg cap, daily-loss kill, cadence
            state.py    on-disk positions / kill switch / high-water mark
proxy/      mcp_proxy.py local MCP proxy (gates + review-then-place + read helpers)
            client.py    low-level streamable-HTTP MCP client (network send stubbed)
            oauth.py     OAuth token holder/refresh (stub)
```

## The strategy (frozen)

Dual-momentum rotation over the 11 sector SPDRs + BIL. Hold the top 4 by blended
3/6/12-month momentum, equal-weight; any pick weaker than BIL over 12 months forfeits
its slot to BIL (the crash circuit breaker). Rebalance **month-end only** — the sweep
showed the date is sensitive, so the cadence is enforced, not optional.

Validated edge: cut historical max drawdown ~23 pts for ~0.4 pts of CAGR, higher Sharpe.
Known blind spot: a 12-month absolute filter is slow against fast V-shaped crashes (2020).
Size the account to the real max drawdown, not the CAGR.

## Three-layer safety

Every order is gated **three** times:

1. `live/rails.py` (runner) — allow-list, per-leg cap, no-short, daily-loss kill, cadence.
2. `proxy/mcp_proxy.py` (proxy) — independent allow-list + no-short + kill flag, so a
   runner bug can't place an off-list/short/post-kill order.
3. `review_equity_order` (Robinhood, native) — `review_then_place()` simulates the
   order via RH's own pre-trade review, inspects the returned warnings, and only then
   calls `place_equity_order`. Blocking warnings (insufficient funds, restricted,
   halted, margin, PDT, etc.) abort placement.

Nothing reaches Robinhood unless:

1. `--live` is passed, **and**
2. env `LIVE_TRADING=true`, **and**
3. neither kill switch (runner or proxy) is set, **and**
4. every order passes all three layers.

## Tool surface

Built against the community-documented June 2026 RH equity surface (`proxy/client.py`):
reads (`get_accounts`, `get_equity_positions`, `get_equity_quotes`, ...), watchlist,
and trade (`review_equity_order`, `place_equity_order`, `cancel_equity_order`).
**Confirm these by a live tool-surface audit before arming** — ask the connected
agent to list its tools and diff against `client.TOOLS`.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# offline logic test (no network, fake prices):
python3 -m live.runner --source synthetic --force-rebalance

# live data, dry-run (real prices, logs intended orders, sends nothing):
python3 -m live.runner --source yfinance

# arm live (ONLY after the account exists + proxy is wired):
cp .env.example .env   # then edit
LIVE_TRADING=true python3 -m live.runner --source yfinance --live
```

`--force-rebalance` bypasses the month-end guard for testing only; never use it live.

## Secrets

`.env`, `secrets/`, and any `*token*.json` are gitignored. The Robinhood OAuth
tokens live in `secrets/rh_token.json` (chmod 600), produced by the one-time browser
consent when the Agentic account is connected. Never commit them — token-in-history
is permanent.

## What's left before live

1. Open + authenticate the Robinhood Agentic **Trading** account (separate product
   from the Gold/Agentic Credit Card). [application pending]
2. Save the OAuth tokens from the browser consent into `secrets/rh_token.json`;
   implement token refresh in `proxy/oauth.py` against the confirmed RH token endpoint.
3. Drop the `httpx` POST into `proxy/client.call_tool()` (one marked block) and run
   the tool-surface audit to confirm the verb names + the `place_equity_order` /
   `review_equity_order` arg schema (esp. dollar `amount` vs `quantity`).
4. Fund with throwaway size; watch one full month-end cycle in dry-run first.

Tool names, the review-then-place gate, read helpers, and the live execution path
are already wired — steps 2–3 are the only remaining glue, and both need the account.
```
