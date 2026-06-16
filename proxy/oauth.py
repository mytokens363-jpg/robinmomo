"""
OAuth credential handling for the Robinhood Agentic Trading MCP server.

SECURITY: nothing in here may ever be committed with real values. Tokens are read
from the environment / a gitignored token file at runtime. The initial OAuth consent
is a browser flow (desktop-only) that Robinhood requires when you open/connect the
Agentic account — that happens ONCE, out of band; this module only persists and
refreshes the resulting tokens.

Everything below the dashed line is a STUB. The real endpoints, scopes, and refresh
semantics must be confirmed against Robinhood's MCP docs once the account exists.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

# Confirm these against the real RH MCP docs when the account is live.
MCP_TRADING_URL = os.environ.get("RH_MCP_TRADING_URL", "https://agent.robinhood.com/mcp/trading")
TOKEN_FILE = Path(os.environ.get("RH_TOKEN_FILE", "secrets/rh_token.json"))   # gitignored


def _read_token_file() -> dict:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"no token at {TOKEN_FILE}. Complete the Robinhood Agentic browser consent "
            f"first, then save the issued tokens here (see .env.example).")
    return json.loads(TOKEN_FILE.read_text())


def _write_token_file(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    os.chmod(TOKEN_FILE, 0o600)


def get_access_token() -> str:
    """Return a valid bearer token, refreshing if near expiry.

    STUB: the refresh call is not implemented because RH's token endpoint + grant
    type aren't confirmed yet. Today this just returns a stored access_token and
    raises if it looks expired, so you can't accidentally run live on a dead token.
    """
    tok = _read_token_file()
    now = int(time.time())
    if tok.get("expires_at", 0) - now < 60:
        # TODO: POST refresh_token to RH's token endpoint, persist new tokens.
        raise NotImplementedError(
            "token expired/near-expiry and refresh is not wired. Re-run the consent "
            "flow or implement refresh against the confirmed RH token endpoint.")
    return tok["access_token"]


def auth_headers() -> dict[str, str]:
    """Headers to attach to MCP requests. OpenClaw's MCP client supports static
    headers; this is where the bearer is injected (see proxy/mcp_proxy.py)."""
    return {"Authorization": f"Bearer {get_access_token()}"}
