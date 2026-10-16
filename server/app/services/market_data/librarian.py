from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.brokers.tradelocker.adapter import TradeLockerAdapter, get_tradelocker_adapter
from app.config.settings import settings
from app.models.providers import (
    MacroCatalog,
    MacroSeriesResult,
    MarketCandle,
    MarketSeries,
)
from app.services.market_data.candles import normalize_history_payload
from app.services.market_data.history import (
    TIMEFRAME_DURATION_MS,
    estimate_candles,
    normalize_timeframe,
    parse_utc_timestamp,
)
from app.services.providers.errors import ProviderError
from app.services.providers.finnhub import FinnhubClient
from app.services.providers.fred import FredClient
from app.services.watchlist import normalize_pair


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _market_candles(candles: list[Any], *, volume_type: str | None) -> list[MarketCandle]:
    unique = {candle.timestamp: candle for candle in candles}
    return [
        MarketCandle(
            timestamp=_utc(candle.timestamp), open=candle.open, high=candle.high,
            low=candle.low, close=candle.close, volume=candle.volume,
            volume_type=volume_type, complete=None,
        )
        for candle in sorted(unique.values(), key=lambda item: item.timestamp)
    ]


def _response_limit(requested: int | None) -> int:
    limit = requested or settings.market_data_max_response_candles
    if not 1 <= limit <= settings.market_data_max_response_candles:
        raise ProviderError(
            "market_data", "response_too_large",
            f"max_candles must be between 1 and {settings.market_data_max_response_candles}.",
        )
    return limit


async def get_market_series(
    *, symbol: str, timeframe: str, source: str = "tradelocker",
    lookback: int | None = None, start_time: str | None = None,
    end_time: str | None = None, max_candles: int | None = None,
    tradelocker_adapter: TradeLockerAdapter | None = None,
) -> MarketSeries:
    resolution = normalize_timeframe(timeframe)
    response_limit = _response_limit(max_candles)
    count = lookback or settings.market_data_default_candles
    if count > response_limit and start_time is None:
        raise ProviderError(
            source, "response_too_large",
            f"Requested {count} candles exceeds the {response_limit} response limit; use a coarser timeframe or smaller lookback.",
        )
    if start_time and end_time:
        estimated = estimate_candles(
            parse_utc_timestamp(start_time, "start_time"),
            parse_utc_timestamp(end_time, "end_time"), resolution,
        )
        if estimated > response_limit:
            raise ProviderError(
                source, "response_too_large",
                f"Estimated {estimated} candles exceeds the {response_limit} response limit; use a coarser timeframe or narrower range.",
            )

    normalized = normalize_pair(symbol)
    if source == "tradelocker":
        start_ms = parse_utc_timestamp(start_time, "start_time") if start_time else None
        end_ms = parse_utc_timestamp(end_time, "end_time") if end_time else None
        history = await (tradelocker_adapter or get_tradelocker_adapter()).get_candles(
            normalized, resolution, None if start_ms is not None else count,
            start_time_ms=start_ms, end_time_ms=end_ms,
        )
        analysis_rows = history.usable_candles if history.symbol is not None else history.candles
        candles = _market_candles(analysis_rows, volume_type="tick")
        if len(candles) > response_limit:
            raise ProviderError(
                "tradelocker", "response_too_large",
                f"Retrieved {len(candles)} candles exceeds the {response_limit} response limit; use a coarser timeframe or narrower range.",
            )
        return MarketSeries(
            symbol=symbol, normalized_symbol=normalized.replace("/", ""), timeframe=resolution,
            source="tradelocker", feed="connected_account", provider_symbol=str(history.instrument_id),
            requested_start=_utc(history.requested_start_ms), requested_end=_utc(history.requested_end_ms),
            actual_start=candles[0].timestamp if candles else None,
            actual_end=candles[-1].timestamp if candles else None,
            candles_returned=len(candles), estimated_candles=history.estimated_candles,
            batches_requested=history.batches_requested, complete=history.complete,
            warning=history.warning, stop_reason=history.stop_reason,
            malformed_candles_discarded=history.malformed_discarded,
            retrieved_at=datetime.now(timezone.utc), candles=candles,
            candle_result=history.canonical_dict(),
        )
    if source == "finnhub":
        duration = TIMEFRAME_DURATION_MS[resolution]
        end_ms = parse_utc_timestamp(end_time, "end_time") if end_time else int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = parse_utc_timestamp(start_time, "start_time") if start_time else end_ms - duration * count
        provider_resolution = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1H": "60", "4H": "240", "1D": "D", "1W": "W", "1M": "M"}[resolution]
        clean = normalized.replace("/", "_")
        provider_symbol = symbol if ":" in symbol else f"OANDA:{clean}"
        client = FinnhubClient()
        try:
            payload = await client.forex_candles(provider_symbol, provider_resolution, start_ms // 1000, end_ms // 1000)
        finally:
            await client.aclose()
        raw, discarded = normalize_history_payload(payload)
        raw = [candle for candle in raw if start_ms <= candle.timestamp <= end_ms]
        candles = _market_candles(raw, volume_type="provider_reported")
        if len(candles) > response_limit:
            raise ProviderError(
                "finnhub", "response_too_large",
                f"Retrieved {len(candles)} candles exceeds the {response_limit} response limit; use a coarser timeframe or narrower range.",
            )
        status = payload.get("s") if isinstance(payload, dict) else None
        complete = bool(candles) and candles[0].timestamp <= _utc(start_ms + duration)
        return MarketSeries(
            symbol=symbol, normalized_symbol=normalized.replace("/", ""), timeframe=resolution,
            source="finnhub", feed="secondary_context", provider_symbol=provider_symbol,
            requested_start=_utc(start_ms), requested_end=_utc(end_ms),
            actual_start=candles[0].timestamp if candles else None,
            actual_end=candles[-1].timestamp if candles else None,
            candles_returned=len(candles), estimated_candles=estimate_candles(start_ms, end_ms, resolution),
            batches_requested=1, complete=complete,
            warning=None if complete else f"Finnhub returned incomplete forex history (status={status}).",
            stop_reason="range_covered" if complete else "provider_no_older_history",
            malformed_candles_discarded=discarded, retrieved_at=datetime.now(timezone.utc), candles=candles,
        )
    raise ProviderError("market_data", "invalid_request", "source must be 'tradelocker' or 'finnhub'.")


async def watchlist_market_data(
    symbols: list[str], timeframe: str, lookback: int, fields: list[str] | None,
    max_symbols: int,
) -> dict[str, Any]:
    if not 1 <= max_symbols <= 20:
        raise ValueError("max_symbols must be between 1 and 20.")
    selected = symbols[:max_symbols]
    close_only = not fields or set(fields) <= {"timestamp", "close"}
    point_limit = min(lookback, 200)
    results = []
    for symbol in selected:
        try:
            series = await get_market_series(
                symbol=symbol, timeframe=timeframe, source="tradelocker",
                lookback=point_limit, max_candles=point_limit,
            )
            item = series.model_dump(mode="json")
            if close_only:
                item["candles"] = [
                    {"timestamp": candle.timestamp.isoformat().replace("+00:00", "Z"), "close": candle.close}
                    for candle in series.candles
                ]
            elif fields:
                allowed = {"timestamp", "open", "high", "low", "close", "volume"}
                selected_fields = ({"timestamp"} | set(fields)) & allowed
                item["candles"] = [
                    {key: value for key, value in candle.items() if key in selected_fields}
                    for candle in item["candles"]
                ]
            results.append(item)
        except Exception as exc:
            if isinstance(exc, ProviderError):
                results.append({"symbol": symbol, **exc.as_dict()})
            else:
                results.append({"symbol": symbol, "status": "error", "error": "upstream_failure", "message": "Market data retrieval failed."})
    return {
        "source": "tradelocker", "symbols_requested": len(selected), "results": results,
        "warning": "Watchlist lookback is capped at 200 points per symbol." if lookback > 200 else None,
    }


def macro_catalog() -> MacroCatalog:
    try:
        return MacroCatalog(currencies=json.loads(settings.macro_catalog_json))
    except (json.JSONDecodeError, TypeError):
        raise ValueError("MACRO_CATALOG_JSON must contain a currency-to-series mapping.") from None


async def get_macro_results(
    series_ids: list[str], observation_start: date | None, observation_end: date | None,
    realtime_start: date | None, realtime_end: date | None, limit: int,
) -> list[MacroSeriesResult]:
    client = FredClient()
    try:
        results = []
        for series_id in series_ids[:10]:
            metadata = await client.series_metadata(series_id)
            observations = await client.observations(
                series_id, observation_start=observation_start, observation_end=observation_end,
                realtime_start=realtime_start, realtime_end=realtime_end, limit=limit,
            )
            results.append(MacroSeriesResult(metadata=metadata, observations=observations))
        return results
    finally:
        await client.aclose()
