from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.market import Candle
from app.services import desk_tools as tools
from app.services.market_data.candles import normalize_candle, normalize_history_payload
from app.services.market_data.history import (
    MAX_CANDLES,
    PaginatedCandleResult,
    TIMEFRAME_DURATION_MS,
    aggregate_hourly_candles_to_utc_days,
    aggregate_complete_candles,
    get_candles_paginated,
    normalize_timeframe,
    validate_candle_result,
)


HOUR = TIMEFRAME_DURATION_MS["1H"]
END = int(datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp() * 1000)


def raw(timestamp: int, *, abbreviated: bool = True) -> dict:
    values = {"timestamp": timestamp, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "volume": 10}
    if not abbreviated:
        return values
    return {"t": timestamp, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "v": 10}


def provider(timestamps: list[int], calls: list[tuple[int, int]], *, fail_after: int | None = None):
    async def fetch(start: int, end: int):
        calls.append((start, end))
        if fail_after is not None and len(calls) > fail_after:
            raise TimeoutError("provider timeout")
        selected = [value for value in timestamps if start <= value <= end][-300:]
        return [raw(value) for value in selected]
    return fetch


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [25, 300])
async def test_one_batch_and_exactly_300(count):
    start = END - HOUR * count
    timestamps = [start + HOUR * index for index in range(1, count + 1)]
    calls = []
    result = await get_candles_paginated(
        instrument_id="77", timeframe="1H", start_time_ms=start, end_time_ms=END,
        requested_count=count, fetch_page=provider(timestamps, calls),
    )
    assert len(result.candles) == count
    assert result.complete
    assert result.batches_requested == 1


@pytest.mark.asyncio
async def test_three_month_hourly_paginates_backward_deduplicates_and_sorts():
    count = 24 * 91
    start = END - HOUR * count
    timestamps = [start + HOUR * index for index in range(1, count + 1)]
    calls = []

    async def inclusive_provider(page_start: int, page_end: int):
        calls.append((page_start, page_end))
        selected = [value for value in timestamps if page_start <= value <= page_end][-300:]
        if selected:
            selected.append(selected[-1])
        return [raw(value) for value in reversed(selected)]

    result = await get_candles_paginated(
        instrument_id="77", timeframe="1h", start_time_ms=start, end_time_ms=END,
        fetch_page=inclusive_provider,
    )
    assert len(result.candles) == count > 300
    assert result.complete and result.batches_requested == 8
    assert [c.timestamp for c in result.candles] == sorted(set(timestamps))
    assert all(calls[index + 1][1] < calls[index][1] for index in range(len(calls) - 1))



@pytest.mark.asyncio
async def test_range_filter_empty_second_page_and_incomplete_metadata():
    start = END - HOUR * 600
    available = [END - HOUR * index for index in range(200)]
    calls = []
    result = await get_candles_paginated(
        instrument_id="77", timeframe="1H", start_time_ms=start, end_time_ms=END,
        fetch_page=provider(available + [start - HOUR, END + HOUR], calls),
    )
    assert result.batches_requested == 2
    assert not result.complete
    assert result.stop_reason == "provider_no_older_history"
    assert all(start <= candle.timestamp <= END for candle in result.candles)
    assert "no older" in result.warning.lower()


@pytest.mark.asyncio
async def test_stalled_oldest_timestamp_stops_without_looping():
    start = END - HOUR * 600
    fixed = [END - HOUR * index for index in range(300)]
    calls = []

    async def ignores_boundaries(page_start: int, page_end: int):
        calls.append((page_start, page_end))
        return [raw(value) for value in fixed]

    result = await get_candles_paginated(
        instrument_id="77", timeframe="1H", start_time_ms=start, end_time_ms=END,
        fetch_page=ignores_boundaries,
    )
    assert result.stop_reason == "oldest_timestamp_stalled"
    assert result.batches_requested == 2
    assert len(result.candles) == 300


@pytest.mark.asyncio
async def test_maximum_candle_and_page_limits():
    with pytest.raises(ValueError, match="safety limit"):
        await get_candles_paginated(
            instrument_id="77", timeframe="1H", start_time_ms=END - HOUR * (MAX_CANDLES + 1),
            end_time_ms=END, fetch_page=provider([], []),
        )
    timestamps = [END - HOUR * index for index in range(1000)]
    result = await get_candles_paginated(
        instrument_id="77", timeframe="1H", start_time_ms=END - HOUR * 1000,
        end_time_ms=END, fetch_page=provider(timestamps, []), max_pages=2,
    )
    assert result.stop_reason == "max_pages_reached"
    assert not result.complete


@pytest.mark.asyncio
async def test_partial_result_survives_later_upstream_failure():
    start = END - HOUR * 600
    timestamps = [start + HOUR * index for index in range(1, 601)]
    result = await get_candles_paginated(
        instrument_id="77", timeframe="1H", start_time_ms=start, end_time_ms=END,
        fetch_page=provider(timestamps, [], fail_after=1),
    )
    assert len(result.candles) == 300
    assert not result.complete and result.stop_reason == "upstream_error"
    assert result.correlation_id and "partial" in result.warning.lower()


def test_normalizes_abbreviated_expanded_seconds_missing_volume_and_rejects_malformed():
    seconds = END // 1000
    abbreviated = normalize_candle({"t": seconds, "o": 1, "h": 2, "l": 0.5, "c": 1.5})
    expanded = normalize_candle(raw(END, abbreviated=False))
    assert abbreviated.timestamp == END and abbreviated.volume == 0
    assert expanded.model_dump() == raw(END, abbreviated=False)
    for bad in (
        {"t": END, "o": "not-a-number", "h": 2, "l": 1, "c": 1.5},
        {"t": END, "o": 1, "h": 0, "l": 1, "c": 1.5},
        {"o": 1, "h": 2, "l": 1, "c": 1.5},
    ):
        with pytest.raises(ValueError):
            normalize_candle(bad)
    numeric = normalize_candle({"t": str(seconds), "o": "0", "h": "2", "l": "0", "c": "1.5", "v": "0"})
    assert numeric.timestamp == END and numeric.open == numeric.low == numeric.volume == 0


def test_malformed_history_rows_are_discarded_at_boundary():
    candles, discarded = normalize_history_payload([raw(END), {"t": END - HOUR, "o": 1}])
    assert len(candles) == 1 and discarded == 1
    assert set(candles[0].model_dump()) == {"timestamp", "open", "high", "low", "close", "volume"}


@pytest.mark.parametrize("alias", ["1D", "1d", "D", "day", "daily", "1440"])
def test_daily_timeframe_aliases_send_documented_resolution(alias):
    assert normalize_timeframe(alias) == "1D"


@pytest.mark.parametrize(("internal", "provider"), [
    ("1d", "1D"), ("4h", "4H"), ("1h", "1H"), ("15m", "15m"),
    ("30m", "30m"), ("5m", "5m"), ("1m", "1m"), ("1w", "1W"),
])
def test_all_canonical_timeframes_use_exact_tradelocker_values(internal, provider):
    assert normalize_timeframe(internal) == provider


def test_complete_hourly_broker_rows_aggregate_to_utc_daily_ohlcv():
    day = END - END % TIMEFRAME_DURATION_MS["1D"]
    rows = [Candle(timestamp=day + HOUR * index, open=1 + index, high=2 + index,
                   low=.5 + index, close=1.5 + index, volume=index)
            for index in range(24)]
    daily, incomplete = aggregate_hourly_candles_to_utc_days(rows, required_count=1)
    assert incomplete == []
    assert [row.timestamp for row in daily] == [day]
    assert daily[0].open == 1 and daily[0].close == 24.5
    assert daily[0].high == 25 and daily[0].low == .5
    assert daily[0].volume == sum(range(24))


def test_incomplete_utc_day_is_rejected_from_aggregation():
    day = END - END % TIMEFRAME_DURATION_MS["1D"]
    rows = [Candle(timestamp=day + HOUR * index, open=1, high=2, low=.5, close=1.5, volume=0)
            for index in range(23)]
    daily, incomplete = aggregate_hourly_candles_to_utc_days(rows, required_count=1)
    assert daily == []
    assert incomplete == [datetime.fromtimestamp(day / 1000, timezone.utc).date().isoformat()]


def _validated(timeframe, timestamps, *, requested, minimum, now):
    resolution = normalize_timeframe(timeframe)
    result = PaginatedCandleResult(
        instrument_id="77", timeframe=resolution, requested_start_ms=timestamps[0],
        requested_end_ms=now, estimated_candles=requested,
        candles=[Candle(timestamp=stamp, open=1, high=2, low=.5, close=1.5, volume=0)
                 for stamp in timestamps],
        batches_requested=2, rows_received=len(timestamps), raw_count=len(timestamps),
        stop_reason="provider_no_older_history",
    )
    return validate_candle_result(
        result, symbol="EURUSD", requested_timeframe=timeframe,
        requested_count=requested, minimum_usable=minimum, now_ms=now,
    )


def test_184_valid_four_hour_candles_are_partial_but_strategy_sufficient():
    duration = TIMEFRAME_DURATION_MS["4H"]
    start = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [start + duration * index for index in range(184)]
    result = _validated("4h", timestamps, requested=250, minimum=50,
                        now=timestamps[-1] + duration)
    assert result.complete and result.status == "partial"
    assert result.usable_count == 184
    assert result.warnings == ["partial_usable_history"]
    assert "provider_request_failed" not in result.blocking_reasons


def test_forming_candle_is_preserved_but_excluded_from_analysis():
    duration = TIMEFRAME_DURATION_MS["1H"]
    start = int(datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [start + duration * index for index in range(51)]
    result = _validated("1h", timestamps, requested=51, minimum=50,
                        now=timestamps[-1] + duration // 2)
    assert result.complete and result.forming_candle_excluded
    assert result.forming_candle.timestamp == timestamps[-1]
    assert result.usable_count == 50


def test_weekend_gaps_are_accepted_but_active_session_gaps_block():
    duration = TIMEFRAME_DURATION_MS["4H"]
    friday = int(datetime(2026, 7, 17, 20, tzinfo=timezone.utc).timestamp() * 1000)
    monday = int(datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp() * 1000)
    weekend = _validated("4h", [friday, monday], requested=2, minimum=2,
                         now=monday + duration)
    assert weekend.complete and weekend.accepted_market_closure_gaps > 0
    assert weekend.unexpected_gap_count == 0

    monday_start = int(datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp() * 1000)
    active_gap = _validated("4h", [monday_start, monday_start + duration * 2],
                            requested=2, minimum=2, now=monday_start + duration * 3)
    assert not active_gap.complete
    assert active_gap.blocking_reasons == ["missing_recent_intervals"]
    assert active_gap.unexpected_gap_count == 1


def test_one_hour_to_four_hour_aggregation_requires_all_constituents():
    hour = TIMEFRAME_DURATION_MS["1H"]
    start = int(datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [Candle(timestamp=start + hour * index, open=1 + index, high=2 + index,
                   low=.5 + index, close=1.5 + index, volume=1) for index in range(8)]
    aggregated, incomplete = aggregate_complete_candles(
        rows, source_timeframe="1h", target_timeframe="4h", required_count=2
    )
    assert len(aggregated) == 2 and incomplete == []
    assert aggregated[0].open == 1 and aggregated[0].close == 4.5
    assert aggregated[0].volume == 4
    rejected, incomplete = aggregate_complete_candles(
        rows[:-1], source_timeframe="1h", target_timeframe="4h", required_count=2
    )
    assert len(rejected) == 1 and incomplete


@pytest.mark.asyncio
async def test_mcp_explicit_range_overrides_lookback_and_serializes_metadata(monkeypatch):
    captured = {}

    class Result:
        def model_dump(self, mode):
            return {"source": "tradelocker", "complete": True}

    async def market_source(**kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(tools, "_missing_user_connection", lambda: None)
    monkeypatch.setattr(tools, "get_market_series", market_source)
    result = await tools.get_my_tradelocker_candles(
        "EUR/USD", "1H", lookback=5,
        start_time="2026-07-12T23:00:00Z", end_time="2026-07-13T00:00:00Z",
    )
    assert captured["lookback"] == 5
    assert captured["start_time"] and captured["end_time"]
    assert result["complete"]


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_range_and_non_utc_time(monkeypatch):
    monkeypatch.setattr(tools, "_missing_user_connection", lambda: None)
    invalid_range = await tools.get_my_tradelocker_candles(
        "EUR/USD", "1H", start_time="2026-07-13T01:00:00Z", end_time="2026-07-13T00:00:00Z"
    )
    non_utc = await tools.get_my_tradelocker_candles(
        "EUR/USD", "1H", start_time="2026-07-12T23:00:00-05:00"
    )
    assert invalid_range["error"] == "invalid_request"
    assert "earlier" in invalid_range["message"]
    assert non_utc["error"] == "invalid_request"
    assert "UTC" in non_utc["message"]
