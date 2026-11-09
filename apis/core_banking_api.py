"""
Mock Core Banking REST API (FastAPI).

Implements the APIs required by the task (section 4.6):
    GetAccountBalanceAPI, GetCardTransactionsAPI, GetStatementAPI,
    ValidateAccountAPI, ValidateCardAPI   (+ GetCustomerByEmailAPI)

Run standalone:
    uvicorn apis.core_banking_api:app --port 8001
Swagger UI:
    http://127.0.0.1:8001/docs
"""
import asyncio
import logging
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apis import core_banking_service as svc
from config.settings import get_settings
from utils.masking import mask_text

log = logging.getLogger("core_banking_api")

app = FastAPI(
    title="Mock Core Banking API",
    version="1.0.0",
    description="Simulated core banking system used by the email automation agents. No real data.",
)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class AccountRequest(BaseModel):
    account_number: str = Field(..., examples=["1234567890"])


class CardRequest(BaseModel):
    card_number: str = Field(..., examples=["4567XXXX8901"])


class CardTransactionsRequest(CardRequest):
    count: int = Field(5, ge=1, le=50)


class StatementRequest(AccountRequest):
    period_start: str = Field(..., examples=["2026-04-01"])
    period_end: str = Field(..., examples=["2026-04-30"])


class CustomerEmailRequest(BaseModel):
    email: str = Field(..., examples=["john.doe@example.com"])


# --------------------------------------------------------------------------- #
# Chaos + demo simulation
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    rate = get_settings().core_api_failure_rate
    if request.url.path.startswith("/api/") and rate > 0 and random.random() < rate:
        log.warning("Chaos: injected 503 for %s", request.url.path)
        return JSONResponse(status_code=503, content={"error": {"code": "SERVICE_UNAVAILABLE",
                                                                 "message": "Core system temporarily unavailable"}})
    return await call_next(request)


async def _simulate(account_number: str):
    """Accounts 5555555555 (slow -> timeout) and 6666666666 (HTTP 500)."""
    mode = svc.simulation_mode(account_number)
    if mode == "timeout":
        await asyncio.sleep(get_settings().core_api_timeout_seconds + 2)
    elif mode == "error":
        raise svc.CoreBankingError("INTERNAL_ERROR", "Core banking host error", 500)


@app.exception_handler(svc.CoreBankingError)
async def core_error_handler(_: Request, exc: svc.CoreBankingError):
    return JSONResponse(status_code=exc.http_status, content={"error": {"code": exc.code, "message": exc.message}})


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "UP", "service": "mock-core-banking"}


@app.post("/api/v1/ValidateAccountAPI", tags=["Validation"])
async def validate_account(req: AccountRequest):
    await _simulate(req.account_number)
    log.info("ValidateAccountAPI %s", mask_text(req.account_number))
    return svc.validate_account(req.account_number)


@app.post("/api/v1/ValidateCardAPI", tags=["Validation"])
def validate_card(req: CardRequest):
    log.info("ValidateCardAPI %s", mask_text(req.card_number))
    return svc.validate_card(req.card_number)


@app.post("/api/v1/GetAccountBalanceAPI", tags=["Accounts"])
async def get_account_balance(req: AccountRequest):
    await _simulate(req.account_number)
    return svc.get_account_balance(req.account_number)


@app.post("/api/v1/GetCardTransactionsAPI", tags=["Cards"])
def get_card_transactions(req: CardTransactionsRequest):
    return svc.get_card_transactions(req.card_number, req.count)


@app.post("/api/v1/GetStatementAPI", tags=["Accounts"])
async def get_statement(req: StatementRequest):
    await _simulate(req.account_number)
    return svc.get_statement(req.account_number, req.period_start, req.period_end)


@app.post("/api/v1/GetCustomerByEmailAPI", tags=["Customers"])
def get_customer_by_email(req: CustomerEmailRequest):
    return svc.get_customer_by_email(req.email)
