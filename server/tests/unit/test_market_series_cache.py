from datetime import datetime, timezone

from app.models.providers import MarketCandle, MarketSeries
from app.services.market_data.series_cache import MarketSeriesCache


def series() -> MarketSeries:
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    candle = MarketCandle(timestamp=now, open=1.1, high=1.2, low=1.0, close=1.15)
    return MarketSeries(
        symbol="EUR/USD", normalized_symbol="EURUSD", timeframe="1H", source="tradelocker",
        actual_start=now, actual_end=now, candles_returned=1, complete=True,
        retrieved_at=now, candles=[candle],
    )


def test_cache_expires_and_reports_expiry():
    clock = [10.0]
    cache = MarketSeriesCache(ttl_seconds=2, max_items=10, clock=lambda: clock[0])
    entry = cache.put("user-a", series())
    clock[0] = 12.0
    assert cache.get("user-a", entry.series_id) == ("expired", None)


def test_expiry_remains_distinct_after_cleanup():
    clock = [10.0]
    cache = MarketSeriesCache(ttl_seconds=2, max_items=10, clock=lambda: clock[0])
    entry = cache.put("user-a", series())
    clock[0] = 12.0
    assert len(cache) == 0
    assert cache.get("user-a", entry.series_id) == ("expired", None)


def test_cache_enforces_maximum_size_by_oldest_entry():
    cache = MarketSeriesCache(ttl_seconds=60, max_items=2)
    first = cache.put("user-a", series())
    cache.put("user-a", series())
    cache.put("user-a", series())
    assert len(cache) == 2
    assert cache.get("user-a", first.series_id) == ("not_found", None)


def test_cache_is_user_scoped():
    cache = MarketSeriesCache(ttl_seconds=60, max_items=2)
    entry = cache.put("user-a", series())
    status, value = cache.get("user-b", entry.series_id)
    assert status == "access_denied" and value is None
