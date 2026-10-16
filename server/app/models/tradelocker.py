from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator


class TradeLockerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradeLockerAccountIdentity(TradeLockerModel):
    account_id: str
    account_number: str
    name: str | None = None
    currency: str | None = None
    account_alias: str | None = None
    environment: Literal["demo", "live", "unknown"]
    active: bool


class TradeLockerTodayStatus(TradeLockerModel):
    gross: float
    net: float
    fees: float
    volume: float
    trades_count: int


class TradeLockerMarginStatus(TradeLockerModel):
    initial_requirement: float
    maintenance_requirement: float
    warning_level: float
    stop_out_level: float
    warning_requirement: float
    margin_before_warning: float


class TradeLockerAccountStatus(TradeLockerModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok"] = "ok"
    source: Literal["tradelocker"] = "tradelocker"
    retrieved_at: datetime
    account: TradeLockerAccountIdentity
    balance: float
    projected_balance: float
    available_funds: float
    blocked_balance: float
    cash_balance: float
    withdrawal_available: float
    open_gross_pnl: float
    open_net_pnl: float
    positions_count: int
    pending_orders_count: int
    today: TradeLockerTodayStatus
    margin: TradeLockerMarginStatus

    @field_validator(
        "balance", "projected_balance", "available_funds", "blocked_balance",
        "cash_balance", "withdrawal_available", "open_gross_pnl", "open_net_pnl",
    )
    @classmethod
    def finite_account_number(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("Account numeric values must be finite.")
        return value


class TradeLockerAccountStatusError(TradeLockerModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["unavailable"] = "unavailable"
    source: Literal["tradelocker"] = "tradelocker"
    error: str
    message: str
    setup_url: str | None = None


TRADELOCKER_ACCOUNT_STATUS_OUTPUT_SCHEMA = TypeAdapter(
    TradeLockerAccountStatus | TradeLockerAccountStatusError
).json_schema()
# MCP output schemas must declare an object at the root. Both union branches are objects.
TRADELOCKER_ACCOUNT_STATUS_OUTPUT_SCHEMA["type"] = "object"
