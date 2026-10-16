from datetime import datetime, timezone

import pytest

from app.brokers.tradelocker.client import TradeLockerError
from app.config.settings import settings
from app.services.market_data import service


@pytest.mark.asyncio
async def test_market_data_defaults_to_mock(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "mock")

    candles = await service.get_candles("EUR/USD", "1h", 2)

    assert len(candles) == 2
    assert candles[0].timestamp < candles[1].timestamp


@pytest.mark.asyncio
async def test_tradelocker_market_data_is_used_only_when_selected(monkeypatch):
    class Adapter:
        async def get_candles(self, pair, timeframe, lookback):
            timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
            return {
                "d": {
                    "t": [timestamp],
                    "o": [1.0],
                    "h": [1.2],
                    "l": [0.9],
                    "c": [1.1],
                    "v": [10],
                }
            }

    monkeypatch.setattr(settings, "market_data_provider", "tradelocker")
    monkeypatch.setattr(service, "get_tradelocker_adapter", lambda: Adapter())

    candles = await service.get_candles("EUR/USD", "1H", 1)

    assert candles[0].close == 1.1
    assert candles[0].volume == 10


@pytest.mark.asyncio
async def test_unusable_tradelocker_history_fails_safely(monkeypatch):
    class Adapter:
        async def get_candles(self, pair, timeframe, lookback):
            return {"status": "not_implemented"}

    monkeypatch.setattr(settings, "market_data_provider", "tradelocker")
    monkeypatch.setattr(service, "get_tradelocker_adapter", lambda: Adapter())

    with pytest.raises(TradeLockerError, match="no usable candle data"):
        await service.get_candles("EUR/USD", "1H", 10)
