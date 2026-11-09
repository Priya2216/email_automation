"""
MCP (Model Context Protocol) server that exposes the core banking APIs as
tools.

The agents never call the core banking REST API directly; they go through
this MCP server. That keeps the agents decoupled from the backend: swapping
the mock API for a real core banking adapter only changes this file.

Each tool returns the same envelope:
    {"ok": true,  "data": {...}}
    {"ok": false, "error": {"code": "...", "message": "...", "retryable": bool}}

Run standalone (stdio transport, as used by the agents):
    python -m mcp_server.banking_mcp_server
"""
import logging
import sys

import httpx
from mcp.server.mcpserver import MCPServer
from tenacity import (AsyncRetrying, retry_if_exception, stop_after_attempt,
                      wait_exponential)

from config.settings import get_settings

# stdout is the MCP transport, so logs must go to stderr
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
log = logging.getLogger("banking_mcp_server")

settings = get_settings()
mcp = MCPServer(
    name="core-banking",
    instructions="Tools for validating and querying customer accounts and credit cards "
                 "in the (simulated) core banking system.",
)


class _Retryable(Exception):
    pass


async def _post(endpoint: str, payload: dict) -> dict:
    """POST to the core API with timeout + retries on transient failures."""
    url = f"{settings.core_api_url}/api/v1/{endpoint}"
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.core_api_max_retries + 1),
            wait=wait_exponential(multiplier=0.2, max=1.0),
            retry=retry_if_exception(lambda e: isinstance(e, (_Retryable, httpx.ConnectError))),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=settings.core_api_timeout_seconds) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code in (502, 503, 504):
                    raise _Retryable(f"HTTP {resp.status_code}")
                body = resp.json()
                if resp.status_code >= 400:
                    err = body.get("error", {})
                    return {"ok": False, "error": {"code": err.get("code", f"HTTP_{resp.status_code}"),
                                                   "message": err.get("message", resp.text),
                                                   "retryable": resp.status_code >= 500}}
                return {"ok": True, "data": body}
    except httpx.TimeoutException:
        return {"ok": False, "error": {"code": "TIMEOUT", "retryable": True,
                                       "message": f"Core system did not respond within "
                                                  f"{settings.core_api_timeout_seconds}s"}}
    except (_Retryable, httpx.ConnectError) as exc:
        return {"ok": False, "error": {"code": "SERVICE_UNAVAILABLE", "retryable": True,
                                       "message": f"Core system unavailable after retries ({exc})"}}
    except Exception as exc:  # noqa: BLE001 - tools must never crash the server
        log.exception("Unexpected error calling %s", endpoint)
        return {"ok": False, "error": {"code": "UNEXPECTED", "message": str(exc), "retryable": False}}


@mcp.tool()
async def validate_account(account_number: str) -> dict:
    """ValidateAccountAPI - check that an account exists and is active. Returns customer name."""
    return await _post("ValidateAccountAPI", {"account_number": account_number})


@mcp.tool()
async def validate_card(card_number: str) -> dict:
    """ValidateCardAPI - check a credit card (full, masked like 4567XXXX8901, or 9-digit reference)."""
    return await _post("ValidateCardAPI", {"card_number": card_number})


@mcp.tool()
async def get_account_balance(account_number: str) -> dict:
    """GetAccountBalanceAPI - available and ledger balance of an account."""
    return await _post("GetAccountBalanceAPI", {"account_number": account_number})


@mcp.tool()
async def get_card_transactions(card_number: str, count: int = 5) -> dict:
    """GetCardTransactionsAPI - most recent `count` transactions of a credit card."""
    return await _post("GetCardTransactionsAPI", {"card_number": card_number, "count": count})


@mcp.tool()
async def get_statement(account_number: str, period_start: str, period_end: str) -> dict:
    """GetStatementAPI - generate an account statement (PDF) for a period. Dates are YYYY-MM-DD."""
    return await _post("GetStatementAPI", {"account_number": account_number,
                                           "period_start": period_start, "period_end": period_end})


@mcp.tool()
async def get_customer_by_email(email: str) -> dict:
    """Look up the customer profile (name, primary account) registered with an email address."""
    return await _post("GetCustomerByEmailAPI", {"email": email})


if __name__ == "__main__":
    mcp.run("stdio")
