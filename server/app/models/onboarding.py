from enum import StrEnum

from pydantic import BaseModel
from typing import Literal


class TradeLockerConnectionStatus(StrEnum):
    NOT_CONNECTED = "not_connected"
    CONNECTED_NO_ACCOUNT = "connected_no_account"
    READY = "ready"
    INVALID_CREDENTIALS = "invalid_credentials"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class SelectedTradeLockerAccount(BaseModel):
    account_id: str
    account_number: str
    server: str
    environment: Literal["demo", "live"] | None = None
    account_alias: str | None = None


class TradeLockerOnboardingStatus(BaseModel):
    status: TradeLockerConnectionStatus
    connected: bool
    selected_account: SelectedTradeLockerAccount | None = None
    message: str | None = None
    retryable: bool = False
