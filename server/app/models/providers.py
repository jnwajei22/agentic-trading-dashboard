from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MarketCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    volume_type: str | None = None
    complete: bool | None = None

    @model_validator(mode="after")
    def validate_candle(self) -> "MarketCandle":
        values = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("OHLC values must be finite.")
        if self.volume is not None and not math.isfinite(self.volume):
            raise ValueError("Volume must be finite when present.")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("High cannot be below open, close, or low.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Low cannot be above open, close, or high.")
        if self.timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware UTC.")
        self.timestamp = self.timestamp.astimezone(timezone.utc)
        return self


class ClientUsage(BaseModel):
    purpose: str = "chart_and_analysis_input"
    preferred_chart_type: str = "candlestick"
    timestamp_order: str = "oldest_to_newest"
    render_client_side: bool = True
    render_tool: str = "render_market_chart"


class MarketSeries(BaseModel):
    schema_version: str = "1.0"
    symbol: str
    normalized_symbol: str
    timeframe: str
    source: str
    feed: str | None = None
    provider_symbol: str | None = None
    requested_start: datetime | None = None
    requested_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    candles_returned: int
    estimated_candles: int | None = None
    batches_requested: int | None = None
    complete: bool
    warning: str | None = None
    stop_reason: str | None = None
    malformed_candles_discarded: int = 0
    retrieved_at: datetime
    candles: list[MarketCandle]
    client_usage: ClientUsage = Field(default_factory=ClientUsage)
    series_id: str | None = None
    series_expires_at: datetime | None = None
    candle_result: dict[str, Any] | None = None


class EconomicEvent(BaseModel):
    event: str
    country: str | None = None
    currency: str | None = None
    scheduled_at: datetime | None = None
    impact: str | None = None
    period: str | None = None
    previous: float | str | None = None
    estimate: float | str | None = None
    actual: float | str | None = None
    unit: str | None = None
    source: str = "finnhub"


class MarketNewsItem(BaseModel):
    id: str | None = None
    published_at: datetime
    headline: str
    summary: str | None = None
    source_name: str | None = None
    related_symbols: list[str] = Field(default_factory=list)
    url: str | None = None
    source: str = "finnhub"


class MacroSeriesMetadata(BaseModel):
    series_id: str
    title: str
    units: str | None = None
    frequency: str | None = None
    seasonal_adjustment: str | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    last_updated: datetime | None = None
    source: str = "fred"


class MacroObservation(BaseModel):
    series_id: str
    date: date
    value: float | None
    realtime_start: date | None = None
    realtime_end: date | None = None
    source: str = "fred"


class MacroReleaseDate(BaseModel):
    release_id: int
    release_name: str
    date: date
    source: str = "fred"


class ProviderErrorResponse(BaseModel):
    status: str = "error"
    provider: str
    error: Literal[
        "not_configured", "capability_unavailable", "authentication_failed",
        "permission_denied", "rate_limited", "invalid_request", "upstream_timeout",
        "upstream_failure", "partial_data", "response_too_large",
    ]
    message: str
    capability: str | None = None
    retryable: bool
    status_code: int | None = None
    correlation_id: str | None = None


class MacroSeriesResult(BaseModel):
    metadata: MacroSeriesMetadata
    observations: list[MacroObservation]


class MacroCatalog(BaseModel):
    currencies: dict[str, list[str]] = Field(default_factory=dict)


JsonObject = dict[str, Any]
