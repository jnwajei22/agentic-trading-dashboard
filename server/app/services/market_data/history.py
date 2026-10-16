from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.models.market import Candle
from app.config.settings import settings
from app.services.market_data.candles import normalize_history_payload

MAX_CANDLES = settings.market_data_max_retrieval_candles
MAX_BATCH_SIZE = 300
MAX_PAGES = settings.market_data_max_pages

# TradeLocker uses this exact, case-sensitive resolution vocabulary.
TIMEFRAME_DURATION_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "1D": 86_400_000,
    "1W": 604_800_000,
    "1M": 2_592_000_000,
}

# The sole internal-to-provider translation table for every candle consumer.
# TradeLocker resolutions are case-sensitive; minute resolutions use a lower
# case `m` (including 15m), while hour/day/week/month use upper case units.
CANONICAL_TIMEFRAMES = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W", "1mo": "1M",
}
TIMEFRAME_ALIASES = {
    "1M": "1mo", "month": "1mo", "monthly": "1mo",
    "1W": "1w", "week": "1w", "weekly": "1w",
    "1D": "1d", "D": "1d", "d": "1d", "day": "1d", "daily": "1d", "1440": "1d",
    "1H": "1h", "60": "1h", "4H": "4h", "240": "4h",
    "15M": "15m", "15": "15m", "30M": "30m", "30": "30m",
    "5M": "5m", "5": "5m", "1": "1m",
}

logger = logging.getLogger(__name__)
PageFetcher = Callable[[int, int], Awaitable[Any]]


def normalize_timeframe(timeframe: str) -> str:
    candidate = timeframe.strip()
    internal = TIMEFRAME_ALIASES.get(candidate, candidate.lower())
    provider = CANONICAL_TIMEFRAMES.get(internal)
    if provider is None:
        raise ValueError(f"Unsupported TradeLocker timeframe: {timeframe}.")
    return provider


def internal_timeframe(timeframe: str) -> str:
    provider = normalize_timeframe(timeframe)
    return next(key for key, value in CANONICAL_TIMEFRAMES.items() if value == provider)


def estimate_candles(start_time_ms: int, end_time_ms: int, timeframe: str) -> int:
    if start_time_ms >= end_time_ms:
        raise ValueError("start_time must be earlier than end_time.")
    return max(1, math.ceil((end_time_ms - start_time_ms) / TIMEFRAME_DURATION_MS[timeframe]))


def parse_utc_timestamp(value: str, name: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name} must be a valid ISO-8601 UTC timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must include the UTC timezone.")
    return int(parsed.timestamp() * 1000)


def iso_utc(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


@dataclass
class PaginatedCandleResult:
    instrument_id: str
    timeframe: str
    requested_start_ms: int
    requested_end_ms: int
    estimated_candles: int
    candles: list[Candle] = field(default_factory=list)
    batches_requested: int = 0
    complete: bool = False
    warning: str | None = None
    stop_reason: str | None = None
    malformed_discarded: int = 0
    correlation_id: str | None = None
    requested_timeframe: str | None = None
    provider_timeframe_sent: str | None = None
    http_status: int | None = 200
    broker_error_category: str | None = None
    rows_received: int = 0
    mapping_failure: str | None = None
    source: str = "direct"
    fallback_diagnostics: dict[str, Any] | None = None
    symbol: str | None = None
    status: str = "blocked"
    usable_candles: list[Candle] = field(default_factory=list)
    forming_candle: Candle | None = None
    requested_count: int | None = None
    raw_count: int = 0
    duplicate_count: int = 0
    usable_count: int = 0
    latest_raw_timestamp: int | None = None
    latest_complete_timestamp: int | None = None
    forming_candle_excluded: bool = False
    expected_interval_seconds: int | None = None
    gap_count: int = 0
    accepted_market_closure_gaps: int = 0
    unexpected_gap_count: int = 0
    unexpected_gap_ranges: list[dict[str, str]] = field(default_factory=list)
    pagination_complete: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    incomplete_days_excluded: list[str] = field(default_factory=list)
    aggregation_source_timeframe: str | None = None
    cache_hit: bool = False
    cache_fresh: bool = False
    cache_age_seconds: float | None = None
    upstream_request_made: bool = True
    attempts: int = 0
    retry_count: int = 0
    total_backoff_seconds: float = 0.0
    cooldown_until: str | None = None
    cache_rejection_reason: str | None = None
    coalesced_requests: int = 0

    def diagnostics(self) -> dict[str, Any]:
        result = {
            "requested_timeframe": self.requested_timeframe or self.timeframe,
            "provider_timeframe_sent": self.provider_timeframe_sent or self.timeframe,
            "http_status": self.http_status,
            "broker_error_category": self.broker_error_category,
            "rows_received": self.rows_received,
            "mapping_failure": self.mapping_failure,
            "candle_source": self.source,
        }
        if self.fallback_diagnostics is not None:
            result["fallback"] = self.fallback_diagnostics
        return result

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "symbol": self.symbol,
            "requested_timeframe": self.requested_timeframe,
            "provider_timeframe": self.provider_timeframe_sent or self.timeframe,
            "source": "aggregated" if self.source.startswith("aggregated") else "direct",
            "aggregation_source_timeframe": self.aggregation_source_timeframe,
            "candles": [row.model_dump(mode="json") for row in self.usable_candles],
            "forming_candle": self.forming_candle.model_dump(mode="json") if self.forming_candle else None,
            "metadata": {
                "requested_count": self.requested_count,
                "raw_count": self.raw_count,
                "normalized_count": len(self.candles),
                "usable_count": self.usable_count,
                "malformed_count": self.malformed_discarded,
                "duplicate_count": self.duplicate_count,
                "expected_interval_seconds": self.expected_interval_seconds,
                "gap_count": self.gap_count,
                "accepted_market_closure_gaps": self.accepted_market_closure_gaps,
                "unexpected_gap_count": self.unexpected_gap_count,
                "unexpected_gap_ranges": self.unexpected_gap_ranges,
                "forming_candle_excluded": self.forming_candle_excluded,
                "oldest_timestamp": self.usable_candles[0].timestamp if self.usable_candles else None,
                "newest_timestamp": self.latest_raw_timestamp,
                "newest_completed_timestamp": self.latest_complete_timestamp,
                "latest_complete_timestamp": self.latest_complete_timestamp,
                "requests_made": self.batches_requested,
                "rows_received_raw": self.raw_count,
                "rows_after_deduplication": len(self.candles),
                "pagination_complete": self.pagination_complete,
                "termination_reason": self.stop_reason,
                "is_sufficient": self.complete,
                "incomplete_days_excluded": self.incomplete_days_excluded,
                "cache_hit": self.cache_hit,
                "cache_fresh": self.cache_fresh,
                "cache_age_seconds": self.cache_age_seconds,
                "upstream_request_made": self.upstream_request_made,
                "attempts": self.attempts,
                "retry_count": self.retry_count,
                "total_backoff_seconds": self.total_backoff_seconds,
                "cooldown_until": self.cooldown_until,
                "cache_rejection_reason": self.cache_rejection_reason,
                "coalesced_requests": self.coalesced_requests,
            },
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
        }


def _market_closed(timestamp_ms: int) -> bool:
    # Spot FX closes Friday 17:00 New York and reopens Sunday 17:00 New York;
    # converting from UTC through the IANA zone handles DST without hardcoding
    # a seasonally wrong 21:00/22:00 UTC boundary.
    current = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).astimezone(
        ZoneInfo("America/New_York")
    )
    return current.weekday() == 5 or (current.weekday() == 6 and current.hour < 17) or (
        current.weekday() == 4 and current.hour >= 17
    )


def _latest_expected_complete_start(now_ms: int, duration: int) -> int:
    candidate = (now_ms // duration) * duration - duration
    while _market_closed(candidate):
        candidate -= duration
    return candidate


def next_completed_candle_due_ms(newest_completed_timestamp: int, timeframe: str) -> int:
    """Return when the next completed broker candle should be publishable."""
    resolution = normalize_timeframe(timeframe)
    duration = TIMEFRAME_DURATION_MS[resolution]
    next_start = newest_completed_timestamp + duration
    while _market_closed(next_start):
        next_start += duration
    return next_start + duration


def validate_candle_result(
    result: PaginatedCandleResult, *, symbol: str, requested_timeframe: str,
    requested_count: int | None, minimum_usable: int, now_ms: int | None = None,
) -> PaginatedCandleResult:
    """Apply the shared strategy-facing completion contract to broker history."""
    duration = TIMEFRAME_DURATION_MS[result.timeframe]
    now = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    ordered = sorted({row.timestamp: row for row in result.candles}.values(), key=lambda row: row.timestamp)
    result.symbol = symbol.replace("/", "").upper()
    result.requested_timeframe = internal_timeframe(requested_timeframe)
    result.provider_timeframe_sent = result.provider_timeframe_sent or result.timeframe
    result.requested_count = requested_count
    result.candles = ordered
    result.raw_count = max(result.raw_count, result.rows_received + result.malformed_discarded)
    result.latest_raw_timestamp = ordered[-1].timestamp if ordered else None
    usable = list(ordered)
    if usable and usable[-1].timestamp + duration > now:
        result.forming_candle = usable.pop()
        result.forming_candle_excluded = True
    result.usable_candles = usable
    result.usable_count = len(usable)
    result.latest_complete_timestamp = usable[-1].timestamp if usable else None
    result.expected_interval_seconds = duration // 1000

    unexpected: list[dict[str, str]] = []
    accepted = gap_count = 0
    for previous, current in zip(usable, usable[1:]):
        if current.timestamp - previous.timestamp <= duration:
            continue
        missing = range(previous.timestamp + duration, current.timestamp, duration)
        closed = [stamp for stamp in missing if _market_closed(stamp)]
        active = [stamp for stamp in missing if not _market_closed(stamp)]
        gap_count += len(closed) + len(active)
        accepted += len(closed)
        if active:
            unexpected.append({"from": iso_utc(active[0]), "to": iso_utc(active[-1])})
    result.gap_count = gap_count
    result.accepted_market_closure_gaps = accepted
    result.unexpected_gap_count = sum(
        1 + (parse_utc_timestamp(item["to"], "to") - parse_utc_timestamp(item["from"], "from")) // duration
        for item in unexpected
    )
    result.unexpected_gap_ranges = unexpected

    reasons: list[str] = []
    warnings: list[str] = []
    if not ordered:
        reasons.append("no_candles_returned")
    elif not usable or len(usable) < minimum_usable:
        reasons.append("insufficient_usable_candles")
    if result.malformed_discarded:
        (reasons if not usable else warnings).append("malformed_candle_rows")
    if result.unexpected_gap_count:
        reasons.append("missing_recent_intervals")
    if usable:
        expected = _latest_expected_complete_start(now, duration)
        if usable[-1].timestamp < expected:
            stamps = range(usable[-1].timestamp + duration, expected + 1, duration)
            if any(not _market_closed(stamp) for stamp in stamps):
                reasons.append("latest_candle_stale")
    if len(usable) < (requested_count or minimum_usable) and len(usable) >= minimum_usable:
        warnings.append("partial_usable_history")
    if result.stop_reason in {"max_pages_reached", "provider_no_older_history"} and len(usable) < minimum_usable:
        reasons.append("pagination_exhausted")
    result.blocking_reasons = list(dict.fromkeys(reasons))
    result.warnings = list(dict.fromkeys(warnings))
    result.complete = not result.blocking_reasons
    result.pagination_complete = result.stop_reason in {"requested_count_reached", "range_covered"}
    result.status = "blocked" if result.blocking_reasons else "partial" if result.warnings else "ok"
    return result


def aggregate_hourly_candles_to_utc_days(
    candles: list[Candle], *, required_count: int
) -> tuple[list[Candle], list[str]]:
    """Aggregate only complete, canonical UTC days from verified 1H candles."""
    by_day: dict[int, dict[int, Candle]] = {}
    for candle in candles:
        day_start = candle.timestamp - (candle.timestamp % TIMEFRAME_DURATION_MS["1D"])
        by_day.setdefault(day_start, {})[candle.timestamp] = candle

    daily: list[Candle] = []
    incomplete: list[str] = []
    hour = TIMEFRAME_DURATION_MS["1H"]
    for day_start in sorted(by_day):
        rows = by_day[day_start]
        expected = {day_start + hour * index for index in range(24)}
        if set(rows) != expected:
            incomplete.append(iso_utc(day_start)[:10])
            continue
        ordered = [rows[timestamp] for timestamp in sorted(rows)]
        daily.append(Candle(
            timestamp=day_start,
            open=ordered[0].open,
            high=max(row.high for row in ordered),
            low=min(row.low for row in ordered),
            close=ordered[-1].close,
            volume=sum(row.volume for row in ordered),
        ))
    return daily[-required_count:], incomplete


def aggregate_complete_candles(
    candles: list[Candle], *, source_timeframe: str, target_timeframe: str,
    required_count: int,
) -> tuple[list[Candle], list[str]]:
    """Aggregate aligned lower-timeframe bars without interpolation."""
    source = normalize_timeframe(source_timeframe)
    target = normalize_timeframe(target_timeframe)
    source_ms, target_ms = TIMEFRAME_DURATION_MS[source], TIMEFRAME_DURATION_MS[target]
    if target_ms <= source_ms or target_ms % source_ms:
        raise ValueError("Candle aggregation timeframes are not mathematically compatible.")
    expected_count = target_ms // source_ms
    groups: dict[int, dict[int, Candle]] = {}
    for candle in candles:
        boundary = candle.timestamp - candle.timestamp % target_ms
        groups.setdefault(boundary, {})[candle.timestamp] = candle
    aggregated: list[Candle] = []
    incomplete: list[str] = []
    for boundary in sorted(groups):
        rows = groups[boundary]
        expected = {boundary + source_ms * index for index in range(expected_count)}
        if set(rows) != expected:
            incomplete.append(iso_utc(boundary))
            continue
        ordered = [rows[timestamp] for timestamp in sorted(rows)]
        aggregated.append(Candle(
            timestamp=boundary, open=ordered[0].open,
            high=max(row.high for row in ordered), low=min(row.low for row in ordered),
            close=ordered[-1].close, volume=sum(row.volume for row in ordered),
        ))
    return aggregated[-required_count:], incomplete


async def get_candles_paginated(
    *,
    instrument_id: str,
    timeframe: str,
    start_time_ms: int,
    end_time_ms: int,
    fetch_page: PageFetcher,
    requested_count: int | None = None,
    batch_size: int = MAX_BATCH_SIZE,
    max_candles: int = MAX_CANDLES,
    max_pages: int = MAX_PAGES,
) -> PaginatedCandleResult:
    """Retrieve bounded history backwards and normalize at each page boundary."""
    resolution = normalize_timeframe(timeframe)
    estimated = estimate_candles(start_time_ms, end_time_ms, resolution)
    if requested_count is not None and not 1 <= requested_count <= max_candles:
        raise ValueError(f"lookback must be between 1 and {max_candles}.")
    target = min(estimated, requested_count) if requested_count is not None else estimated
    if target > max_candles or estimated > max_candles:
        raise ValueError(f"Requested candle range exceeds the {max_candles} candle safety limit.")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}.")

    result = PaginatedCandleResult(
        instrument_id=str(instrument_id), timeframe=resolution,
        requested_start_ms=start_time_ms, requested_end_ms=end_time_ms,
        estimated_candles=estimated,
    )
    duration = TIMEFRAME_DURATION_MS[resolution]
    boundary = end_time_ms
    by_timestamp: dict[int, Candle] = {}
    raw_count = 0
    duplicate_count = 0
    previous_oldest: int | None = None
    range_fully_queried = False

    for _ in range(max_pages):
        if boundary < start_time_ms or len(by_timestamp) >= target:
            result.stop_reason = "requested_count_reached" if len(by_timestamp) >= target else "range_covered"
            break
        page_start = max(start_time_ms, boundary - duration * (batch_size - 1))
        result.batches_requested += 1
        try:
            payload = await fetch_page(page_start, boundary)
        except Exception as exc:
            if not by_timestamp:
                raise
            result.stop_reason = "upstream_error"
            result.correlation_id = uuid.uuid4().hex
            logger.warning(
                "TradeLocker candle pagination failed after partial retrieval correlation_id=%s error_type=%s",
                result.correlation_id, type(exc).__name__,
            )
            break

        page, discarded = normalize_history_payload(payload)
        raw_count += len(page) + discarded
        result.malformed_discarded += discarded
        valid = [c for c in page if start_time_ms <= c.timestamp <= end_time_ms]
        if not valid:
            result.stop_reason = "provider_no_older_history"
            break
        oldest = min(c.timestamp for c in valid)
        for candle in valid:
            if candle.timestamp in by_timestamp:
                duplicate_count += 1
            by_timestamp[candle.timestamp] = candle
        if previous_oldest is not None and oldest >= previous_oldest:
            result.stop_reason = "oldest_timestamp_stalled"
            break
        previous_oldest = oldest
        boundary = oldest - 1  # TradeLocker treats `to` as inclusive.
        if requested_count is None and page_start == start_time_ms:
            range_fully_queried = True
            result.stop_reason = "range_covered"
            break
    else:
        result.stop_reason = "max_pages_reached"

    ordered = sorted(by_timestamp.values(), key=lambda candle: candle.timestamp)
    if requested_count is not None and len(ordered) > requested_count:
        ordered = ordered[-requested_count:]
    result.candles = ordered[:max_candles]
    result.raw_count = raw_count
    result.duplicate_count = duplicate_count
    result.rows_received = len(result.candles)
    covered_range = range_fully_queried or (
        bool(ordered) and ordered[0].timestamp <= start_time_ms + duration
    )
    count_complete = requested_count is not None and len(ordered) >= requested_count
    result.complete = covered_range if requested_count is None else count_complete
    if result.complete:
        result.stop_reason = result.stop_reason or "range_covered"
    else:
        reason = {
            "upstream_error": "TradeLocker failed after partial historical retrieval.",
            "max_pages_reached": "The candle-history maximum page limit was reached.",
            "oldest_timestamp_stalled": "TradeLocker did not advance to older candle history.",
            "provider_no_older_history": "TradeLocker returned no older candle history.",
        }.get(result.stop_reason, "The requested candle range was not fully covered.")
        result.warning = f"{reason} Returned {len(ordered)} of approximately {target} candles."
    if result.malformed_discarded:
        result.mapping_failure = f"{result.malformed_discarded} malformed candle row(s)"
        suffix = f" Discarded {result.malformed_discarded} malformed candle(s)."
        result.warning = ((result.warning + suffix) if result.warning else suffix.strip())
    return result
