from datetime import datetime, timezone
from typing import Any

from app.brokers.tradelocker.adapter import get_tradelocker_adapter
from app.brokers.tradelocker.client import TradeLockerError
from app.config.settings import settings
from app.models.market import Candle
from app.services.market_data.mock_provider import DEFAULT_MOCK_CANDLE_PATH, load_mock_candles
from app.services.market_data.candles import normalize_history_payload
from app.services.market_data.history import (
    MAX_CANDLES,
    TIMEFRAME_DURATION_MS,
    PaginatedCandleResult,
    estimate_candles,
    normalize_timeframe,
    parse_utc_timestamp,
    validate_candle_result,
)


def _history_candles(payload: Any) -> list[Candle]:
    if isinstance(payload, PaginatedCandleResult):
        return payload.candles
    candles, _ = normalize_history_payload(payload)
    return sorted(candles, key=lambda candle: candle.timestamp)


async def get_candles(pair: str, timeframe: str, lookback: int = 300) -> list[Candle]:
    return (await get_candle_history(pair=pair, timeframe=timeframe, lookback=lookback)).candles


async def get_candle_history(
    *,
    pair: str,
    timeframe: str,
    lookback: int | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> PaginatedCandleResult:
    """Return canonical candles together with retrieval completion metadata."""
    provider = settings.market_data_provider.lower()
    resolution = normalize_timeframe(timeframe)
    count = 300 if lookback is None else lookback
    if not 1 <= count <= MAX_CANDLES:
        raise ValueError(f"lookback must be between 1 and {MAX_CANDLES}.")
    explicit_start = parse_utc_timestamp(start_time, "start_time") if start_time else None
    explicit_end = parse_utc_timestamp(end_time, "end_time") if end_time else None

    if provider == "tradelocker":
        adapter = get_tradelocker_adapter()
        if explicit_start is not None or explicit_end is not None:
            payload = await adapter.get_candles(
                pair,
                resolution,
                None if explicit_start is not None else count,
                start_time_ms=explicit_start,
                end_time_ms=explicit_end,
            )
        else:
            payload = await adapter.get_candles(pair, resolution, count)
        if isinstance(payload, PaginatedCandleResult):
            result = payload
        else:  # Compatibility for simple adapters used by callers and tests.
            candles = _history_candles(payload)
            end_ms = explicit_end or int(datetime.now(timezone.utc).timestamp() * 1000)
            start_ms = explicit_start or end_ms - TIMEFRAME_DURATION_MS[resolution] * count
            result = PaginatedCandleResult(
                instrument_id=pair.replace("/", ""), timeframe=resolution,
                requested_start_ms=start_ms, requested_end_ms=end_ms,
                estimated_candles=estimate_candles(start_ms, end_ms, resolution),
                candles=candles, batches_requested=1, complete=bool(candles),
                stop_reason="range_covered" if candles else "provider_no_older_history",
            )
        if not result.candles:
            raise TradeLockerError(
                "get_candles", "TradeLocker returned no usable candle data.", code="no_data"
            )
        if result.symbol is None:
            result = validate_candle_result(
                result, symbol=pair, requested_timeframe=timeframe,
                requested_count=count if explicit_start is None else None,
                minimum_usable=count if explicit_start is None else 1,
                now_ms=explicit_end,
            )
        return result
    if provider == "mock":
        available = load_mock_candles(DEFAULT_MOCK_CANDLE_PATH).get(pair, [])
        if not available:
            raise ValueError(f"No candle data found for pair: {pair}")
        end_ms = explicit_end or available[-1].timestamp
        start_ms = explicit_start or (end_ms - TIMEFRAME_DURATION_MS[resolution] * count)
        if start_ms >= end_ms:
            raise ValueError("start_time must be earlier than end_time.")
        if estimate_candles(start_ms, end_ms, resolution) > MAX_CANDLES:
            raise ValueError(f"Requested candle range exceeds the {MAX_CANDLES} candle safety limit.")
        candles = [candle for candle in available if start_ms <= candle.timestamp <= end_ms]
        if explicit_start is None:
            candles = candles[-count:]
        complete = (
            bool(candles)
            and (
                candles[0].timestamp <= start_ms + TIMEFRAME_DURATION_MS[resolution]
                if explicit_start is not None
                else len(candles) >= count
            )
        )
        result = PaginatedCandleResult(
            instrument_id=pair.replace("/", ""), timeframe=resolution,
            requested_start_ms=start_ms, requested_end_ms=end_ms,
            estimated_candles=estimate_candles(start_ms, end_ms, resolution),
            candles=candles, batches_requested=1, complete=complete,
            stop_reason="range_covered" if complete else "provider_no_older_history",
            warning=(
                None if complete else
                f"Mock provider returned {len(candles)} of approximately "
                f"{estimate_candles(start_ms, end_ms, resolution)} candles."
            ),
        )
        return validate_candle_result(
            result, symbol=pair, requested_timeframe=timeframe,
            requested_count=count if explicit_start is None else None,
            minimum_usable=count if explicit_start is None else 1,
            now_ms=end_ms,
        )
    raise ValueError("MARKET_DATA_PROVIDER must be 'mock' or 'tradelocker'.")


def _quote_value(payload: Any, names: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if isinstance(value, (int, float)):
                return float(value)
        for value in payload.values():
            found = _quote_value(value, names)
            if found is not None:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _quote_value(value, names)
            if found is not None:
                return found
    return None


async def get_spread(pair: str) -> float | None:
    """Return ask-minus-bid when the selected provider exposes a usable quote."""
    if settings.market_data_provider.lower() != "tradelocker":
        return None
    try:
        payload = await get_tradelocker_adapter().get_quote(pair)
    except TradeLockerError:
        return None
    ask = _quote_value(payload, ("ask", "askPrice", "ap"))
    bid = _quote_value(payload, ("bid", "bidPrice", "bp"))
    if ask is None or bid is None or ask < bid:
        return None
    return ask - bid
