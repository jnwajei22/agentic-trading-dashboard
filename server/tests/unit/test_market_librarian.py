from datetime import datetime, timezone

import pytest

from app.config.settings import settings
from app.models.market import Candle
from app.services.market_data import librarian
from app.services.market_data.history import PaginatedCandleResult
from app.services.providers.errors import ProviderError


@pytest.mark.asyncio
async def test_tradelocker_market_series_is_canonical_sorted_and_client_ready(monkeypatch):
    values = [
        Candle(timestamp=1_783_890_000_000 + index * 3_600_000, open=1.1, high=1.2, low=1.0, close=1.15, volume=10)
        for index in reversed(range(3))
    ]
    result = PaginatedCandleResult(
        instrument_id="77", timeframe="1H", requested_start_ms=values[-1].timestamp,
        requested_end_ms=values[0].timestamp, estimated_candles=3, candles=values,
        batches_requested=2, complete=False, stop_reason="upstream_error", warning="partial",
    )

    class Adapter:
        async def get_candles(self, symbol, timeframe, lookback, **kwargs):
            assert lookback == 3
            return result

    monkeypatch.setattr(librarian, "get_tradelocker_adapter", lambda: Adapter())
    series = await librarian.get_market_series(symbol="EURUSD", timeframe="1h", lookback=3)
    assert series.source == "tradelocker" and not series.complete
    assert [c.timestamp for c in series.candles] == sorted(c.timestamp for c in series.candles)
    assert series.client_usage.render_client_side
    assert series.candles[0].timestamp.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_response_limit_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, "market_data_max_response_candles", 2000)
    with pytest.raises(ProviderError) as caught:
        await librarian.get_market_series(symbol="EURUSD", timeframe="1H", lookback=2001)
    assert caught.value.response.error == "response_too_large"


def test_no_chart_dependencies_or_storage():
    from pathlib import Path
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "matplotlib" not in requirements and "mplfinance" not in requirements
    assert not Path("app/services/charting").exists()
    assert not Path("storage/charts").exists()
