from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.models.market import Candle
from app.services.market_data.candle_cache import CandleCacheKey, DurableCandleCache
from app.services.market_data.candles import normalize_candle
from app.services.market_data.history import TIMEFRAME_DURATION_MS, normalize_timeframe


class ImmediateLimiter:
    def __init__(self): self.acquires = 0; self.cooldowns = []
    async def acquire(self, connection_id):
        self.acquires += 1
        return SimpleNamespace(connection_id=connection_id, owner="test", waited_seconds=0.0)
    def release(self, lease): pass
    def set_cooldown(self, connection_id, seconds):
        self.cooldowns.append(seconds)
        return datetime.now(timezone.utc).timestamp() + seconds


def client(cache, limiter, *, user="user-a", account="account-a", connection="connection-a"):
    return TradeLockerClient(
        base_url="https://demo.tradelocker.test/backend-api", username="u", password="p",
        server="Demo", account_id=account, account_number="7",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        cache_user_id=user, cache_connection_id=connection,
        cache_account_record_id=f"record-{account}", candle_cache_store=cache,
        request_limiter=limiter,
    )


def rows(timeframe, count, *, end=None):
    duration = TIMEFRAME_DURATION_MS[normalize_timeframe(timeframe)]
    end = end or (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    return [{"t": end - duration * (count - index), "o": 1, "h": 2,
             "l": .5, "c": 1.5, "v": 0} for index in range(count)]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeframe", ["1d", "4h", "1h", "15m"])
async def test_success_populates_durable_cache_and_later_process_reuses_it(tmp_path, timeframe):
    cache_a = DurableCandleCache(tmp_path / "shared.db")
    limiter = ImmediateLimiter()
    first = client(cache_a, limiter)
    history_rows = rows(timeframe, 50)
    end = history_rows[-1]["t"] + TIMEFRAME_DURATION_MS[normalize_timeframe(timeframe)]
    async def resolve(symbol): return 77, 9
    async def history(**kwargs): return history_rows
    first._resolve_instrument = resolve
    first._history_page = history
    initial = await first.get_candles("EURUSD", timeframe, 50, end_time_ms=end, minimum_usable=50)
    await first.aclose()
    assert initial.complete and not initial.cache_hit
    assert limiter.acquires == 0  # monkeypatched page bypasses the limiter in this unit fixture

    cache_b = DurableCandleCache(tmp_path / "shared.db")
    second_limiter = ImmediateLimiter()
    second = client(cache_b, second_limiter)
    second._resolve_instrument = resolve
    async def no_history(**kwargs): raise AssertionError("fresh cache must bypass TradeLocker")
    second._history_page = no_history
    reused = await second.get_candles("EURUSD", timeframe, 50, end_time_ms=end, minimum_usable=50)
    await second.aclose()
    assert reused.complete and reused.cache_hit and reused.cache_fresh
    assert not reused.upstream_request_made and reused.attempts == 0
    assert second_limiter.acquires == 0


def test_cache_keys_isolate_users_accounts_instruments_and_symbols(tmp_path):
    cache = DurableCandleCache(tmp_path / "isolated.db")
    candle = Candle(timestamp=1_800_000_000_000, open=1, high=2, low=.5, close=1.5, volume=0)
    base = dict(user_id="user-a", connection_id="conn-a", account_id="account-a",
                account_number="7", instrument_id="77", symbol="EURUSD", timeframe="1H")
    key = CandleCacheKey(**base)
    cache.put(key, [candle], source="direct", metadata={"complete": True})
    assert cache.get(key) is not None
    for changed in ({"user_id": "user-b"}, {"account_id": "account-b"},
                    {"account_number": "8"}, {"instrument_id": "88"},
                    {"symbol": "GBPUSD"},
                    {"connection_id": "conn-b"}):
        assert cache.get(CandleCacheKey(**{**base, **changed})) is None


def test_cache_range_coverage_is_explicit(tmp_path):
    cache = DurableCandleCache(tmp_path / "coverage.db")
    values = [normalize_candle(item) for item in rows("1h", 50)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    entry = cache.put(key, values, source="direct", metadata={"requested_count": 50})
    duration = TIMEFRAME_DURATION_MS["1H"]
    assert entry.covers(required_count=10, start_time_ms=values[-10].timestamp,
                        end_time_ms=values[-1].timestamp + duration)
    assert not entry.covers(required_count=10, start_time_ms=values[0].timestamp-duration*10,
                            end_time_ms=values[-1].timestamp + duration)


@pytest.mark.asyncio
async def test_stale_cache_incrementally_refreshes_merges_and_deduplicates(tmp_path):
    cache = DurableCandleCache(tmp_path / "incremental.db")
    limiter = ImmediateLimiter()
    current = client(cache, limiter)
    duration = TIMEFRAME_DURATION_MS["1H"]
    end = (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    cached_rows = [normalize_candle(item) for item in rows("1h", 50, end=end - duration)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    cache.put(key, cached_rows, source="direct", metadata={"is_sufficient": True})
    with sqlite3.connect(tmp_path / "incremental.db") as db:
        db.execute("UPDATE tradelocker_candle_cache SET expires_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    async def resolve(symbol): return 77, 9
    seen = []
    async def history(**kwargs):
        seen.append(kwargs)
        return [cached_rows[-1].model_dump(), Candle(timestamp=end-duration, open=1, high=2, low=.5, close=1.5, volume=0).model_dump()]
    current._resolve_instrument = resolve
    current._history_page = history
    refreshed = await current.get_candles("EURUSD", "1h", 50, end_time_ms=end, minimum_usable=50)
    await current.aclose()
    assert refreshed.complete and not refreshed.cache_hit
    assert len({item.timestamp for item in refreshed.candles}) == 50
    assert seen and seen[0]["start_time_ms"] >= cached_rows[-1].timestamp - duration
    assert refreshed.duplicate_count >= 1


@pytest.mark.asyncio
async def test_larger_window_does_not_incrementally_refresh_from_smaller_cache(tmp_path):
    cache = DurableCandleCache(tmp_path / "larger-window.db")
    current = client(cache, ImmediateLimiter())
    duration = TIMEFRAME_DURATION_MS["1H"]
    end = (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    cached_rows = [normalize_candle(item) for item in rows("1h", 50, end=end-duration)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    cache.put(key, cached_rows, source="direct", metadata={"requested_count": 50})
    with sqlite3.connect(tmp_path / "larger-window.db") as db:
        db.execute("UPDATE tradelocker_candle_cache SET expires_at=?",
                   ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    async def resolve(symbol): return 77, 9
    seen = []
    async def history(**kwargs):
        seen.append(kwargs)
        return rows("1h", 100, end=end)
    current._resolve_instrument = resolve
    current._history_page = history
    result = await current.get_candles("EURUSD", "1h", 100, end_time_ms=end, minimum_usable=50)
    await current.aclose()
    assert result.complete and seen
    assert seen[0]["start_time_ms"] <= end-duration*100


@pytest.mark.asyncio
async def test_rate_limit_uses_sufficient_stale_cache_with_warning(tmp_path, monkeypatch):
    cache = DurableCandleCache(tmp_path / "rate.db")
    current = client(cache, ImmediateLimiter())
    duration = TIMEFRAME_DURATION_MS["1H"]
    end = (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    cached_rows = [normalize_candle(item) for item in rows("1h", 50, end=end)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    cache.put(key, cached_rows, source="direct", metadata={"is_sufficient": True})
    with sqlite3.connect(tmp_path / "rate.db") as db:
        db.execute("UPDATE tradelocker_candle_cache SET expires_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    async def resolve(symbol): return 77, 9
    async def limited(**kwargs):
        raise TradeLockerError("get_candles", "limited", code="tradelocker_rate_limit_exhausted",
                               status_code=429, details={"retryable": True, "suggested_retry_at": "2099-01-01T00:00:00+00:00"})
    current._resolve_instrument = resolve
    current._history_page = limited
    result = await current.get_candles("EURUSD", "1h", 50, end_time_ms=end, minimum_usable=50)
    await current.aclose()
    assert result.complete and result.cache_hit and not result.cache_fresh
    assert "cached_candles_used_after_rate_limit" in result.warnings


@pytest.mark.asyncio
async def test_rate_limit_rejects_noncovering_explicit_range(tmp_path):
    cache = DurableCandleCache(tmp_path / "range-rate.db")
    current = client(cache, ImmediateLimiter())
    duration = TIMEFRAME_DURATION_MS["1H"]
    end = (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    cached_rows = [normalize_candle(item) for item in rows("1h", 50, end=end)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    cache.put(key, cached_rows, source="direct", metadata={"requested_count": 50})
    async def resolve(symbol): return 77, 9
    async def limited(**kwargs):
        raise TradeLockerError("get_candles", "limited",
            code="tradelocker_rate_limit_exhausted", status_code=429,
            details={"retryable": True})
    current._resolve_instrument = resolve
    current._history_page = limited
    with pytest.raises(TradeLockerError) as error:
        await current.get_candles("EURUSD", "1h", None,
            start_time_ms=cached_rows[0].timestamp-duration*20,
            end_time_ms=end, minimum_usable=10)
    await current.aclose()
    assert error.value.details["cache_rejection_reason"] == "cached_range_not_covered"


@pytest.mark.asyncio
async def test_missing_cache_rate_limit_returns_retry_diagnostics(tmp_path):
    current = client(DurableCandleCache(tmp_path / "missing.db"), ImmediateLimiter())
    async def resolve(symbol): return 77, 9
    async def limited(**kwargs):
        raise TradeLockerError("get_candles", "limited", code="tradelocker_rate_limit_exhausted",
                               status_code=429, details={"retryable": True,
                               "suggested_retry_at": "2099-01-01T00:00:00+00:00", "attempts": 3})
    current._resolve_instrument = resolve
    current._history_page = limited
    with pytest.raises(TradeLockerError) as error:
        await current.get_candles("EURUSD", "15m", 50, minimum_usable=50)
    await current.aclose()
    assert error.value.code == "tradelocker_rate_limit_exhausted"
    assert error.value.details["retryable"] is True
    assert error.value.details["cache_available"] is False
    assert error.value.details["cache_rejection_reason"] == "cache_missing"


@pytest.mark.asyncio
async def test_rate_limit_rejects_cache_outside_stale_tolerance(tmp_path, monkeypatch):
    cache = DurableCandleCache(tmp_path / "too-stale.db")
    current = client(cache, ImmediateLimiter())
    duration = TIMEFRAME_DURATION_MS["1H"]
    end = (int(datetime.now(timezone.utc).timestamp() * 1000) // duration) * duration
    cached_rows = [normalize_candle(item) for item in rows("1h", 50, end=end)]
    key = CandleCacheKey("user-a", "connection-a", "account-a", "7", "77", "EURUSD", "1H")
    cache.put(key, cached_rows, source="direct", metadata={"is_sufficient": True},
              fetched_at=datetime.now(timezone.utc)-timedelta(days=2))
    with sqlite3.connect(tmp_path / "too-stale.db") as db:
        db.execute("UPDATE tradelocker_candle_cache SET expires_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    async def resolve(symbol): return 77, 9
    async def limited(**kwargs):
        raise TradeLockerError("get_candles", "limited", code="tradelocker_rate_limit_exhausted",
                               status_code=429, details={"retryable": True})
    current._resolve_instrument = resolve
    current._history_page = limited
    monkeypatch.setattr("app.brokers.tradelocker.client.settings.tradelocker_candle_cache_max_stale_seconds", 60)
    with pytest.raises(TradeLockerError) as error:
        await current.get_candles("EURUSD", "1h", 50, end_time_ms=end, minimum_usable=50)
    await current.aclose()
    assert error.value.details["cache_available"] is True
    assert error.value.details["cache_rejection_reason"] == "cache_exceeds_stale_tolerance"


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_are_coalesced(tmp_path):
    current = client(DurableCandleCache(tmp_path / "coalesce.db"), ImmediateLimiter())
    async def resolve(symbol): return 77, 9
    calls = 0
    async def history(**kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.02)
        return rows("15m", 50)
    current._resolve_instrument = resolve
    current._history_page = history
    end = rows("15m", 50)[-1]["t"] + TIMEFRAME_DURATION_MS["15m"]
    first, second = await asyncio.gather(
        current.get_candles("EURUSD", "15m", 50, end_time_ms=end, minimum_usable=50),
        current.get_candles("EURUSD", "15m", 50, end_time_ms=end, minimum_usable=50),
    )
    await current.aclose()
    assert calls == 1
    assert first.complete and second.complete and second.coalesced_requests == 1


@pytest.mark.asyncio
async def test_http_429_honors_retry_after_and_returns_bounded_diagnostics(tmp_path, monkeypatch):
    limiter = ImmediateLimiter()
    current = client(DurableCandleCache(tmp_path / "retry-after.db"), limiter)
    current._access_token = "test-token"
    current._token_expires_at = time.time() + 3600
    async def resolve(symbol): return 77, 9
    current._resolve_instrument = resolve
    async def send(request):
        return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
    await current._http.aclose()
    current._http = httpx.AsyncClient(
        base_url=current.base_url, transport=httpx.MockTransport(send)
    )
    monkeypatch.setattr("app.brokers.tradelocker.client.settings.tradelocker_rate_limit_max_retries", 0)
    with pytest.raises(TradeLockerError) as error:
        await current.get_candles("EURUSD", "15m", 50, minimum_usable=50)
    await current.aclose()
    assert error.value.code == "tradelocker_rate_limit_exhausted"
    assert error.value.status_code == 429
    assert error.value.details["attempts"] == 1
    assert error.value.details["suggested_retry_at"]
    assert limiter.acquires == 1 and limiter.cooldowns[0] >= 2
