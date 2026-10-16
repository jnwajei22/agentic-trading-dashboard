from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AssetClass = Literal["forex", "equity", "index", "metal", "crypto", "energy", "option", "unknown"]


class CanonicalInstrument(BaseModel):
    canonical_id: str
    symbol: str
    display_symbol: str
    description: str
    asset_class: AssetClass
    exchange: str | None = None
    provider_symbols: dict[str, str] = Field(default_factory=dict)


class MarketSourceStatus(BaseModel):
    provider: str
    status: Literal["available", "unavailable", "unsupported", "stale"]
    updated_at: str | None = None
    message: str | None = None


class MarketSummary(BaseModel):
    instrument: CanonicalInstrument
    quote: dict | None = None
    market_status: dict
    drivers: list[dict] = Field(default_factory=list)
    tradability: dict | None = None
    sources: list[MarketSourceStatus]
    partial: bool = False
