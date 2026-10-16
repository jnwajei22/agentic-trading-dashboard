from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

class Candle(BaseModel):
    """Canonical candle. Timestamps are always Unix epoch milliseconds."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @field_validator("timestamp", mode="before")
    @classmethod
    def canonical_timestamp(cls, value: Any) -> int:
        if isinstance(value, datetime):
            current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return int(current.timestamp() * 1000)
        if isinstance(value, str):
            current = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            return int(current.timestamp() * 1000)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            return int(numeric * 1000 if abs(numeric) < 10_000_000_000 else numeric)
        raise ValueError("Candle timestamp must be an ISO-8601 string or Unix timestamp.")

    @model_validator(mode="after")
    def validate_range(self) -> "Candle":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Candle high cannot be below open, close, or low.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Candle low cannot be above open, close, or high.")
        return self

class ForexPairConfig(BaseModel):
    pair: str
    enabled: bool = True
    max_spread: float | None = None
    allowed_timeframes: list[str] = ["15m", "1h"]
    allowed_sessions: list[str] = ["london", "new_york"]
    max_risk_percent: float = 0.5
    max_open_trades_per_pair: int = 1
    news_blackout_enabled: bool = True
