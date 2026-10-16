from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.config.settings import settings
from app.models.providers import EconomicEvent, MarketNewsItem
from app.services.providers.cache import TTLCache
from app.services.providers.errors import ProviderError


_cache = TTLCache()
_capabilities = TTLCache()


def _cache_key(path: str, params: dict[str, Any]) -> str:
    public = {key: value for key, value in params.items() if key != "token"}
    raw = json.dumps([path, public], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    return None


class FinnhubClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.finnhub_base_url.rstrip("/"),
            timeout=settings.finnhub_timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _validate(self, capability: str) -> None:
        if not settings.finnhub_enabled:
            raise ProviderError("finnhub", "not_configured", "Finnhub is disabled.", capability=capability)
        if not settings.finnhub_api_key:
            raise ProviderError("finnhub", "not_configured", "Finnhub API key is not configured.", capability=capability)
        cached = _capabilities.get(capability)
        if cached:
            raise ProviderError("finnhub", "capability_unavailable", "Finnhub capability is unavailable for the configured plan.", capability=capability, status_code=cached)

    async def _get(self, path: str, capability: str, **params: Any) -> Any:
        self._validate(capability)
        request_params = {**params, "token": settings.finnhub_api_key}
        key = _cache_key(path, request_params)
        cached = _cache.get(key)
        if cached is not None:
            return cached
        for attempt in range(settings.finnhub_max_retries + 1):
            try:
                response = await self._http.get(path, params=request_params)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < settings.finnhub_max_retries:
                    await asyncio.sleep(0.1 * 2**attempt)
                    continue
                category = "upstream_timeout" if isinstance(exc, httpx.TimeoutException) else "upstream_failure"
                raise ProviderError("finnhub", category, "Finnhub request failed temporarily.", capability=capability, retryable=True) from None
            if response.status_code == 429:
                raise ProviderError("finnhub", "rate_limited", "Finnhub rate limit was reached.", capability=capability, retryable=True, status_code=429)
            if response.status_code in {401}:
                raise ProviderError("finnhub", "authentication_failed", "Finnhub authentication failed.", capability=capability, status_code=response.status_code)
            if response.status_code in {403}:
                _capabilities.set(capability, response.status_code, settings.finnhub_cache_ttl_seconds)
                raise ProviderError("finnhub", "capability_unavailable", "Finnhub capability is unavailable for the configured plan.", capability=capability, status_code=response.status_code)
            if 500 <= response.status_code < 600:
                if attempt < settings.finnhub_max_retries:
                    await asyncio.sleep(0.1 * 2**attempt)
                    continue
                raise ProviderError("finnhub", "upstream_failure", "Finnhub is temporarily unavailable.", capability=capability, retryable=True, status_code=response.status_code)
            if response.status_code >= 400:
                raise ProviderError("finnhub", "invalid_request", "Finnhub rejected the request.", capability=capability, status_code=response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError("finnhub", "upstream_failure", "Finnhub returned invalid JSON.", capability=capability, retryable=True) from None
            if isinstance(payload, dict) and payload.get("error"):
                message = str(payload["error"]).lower()
                if "permission" in message or "premium" in message or "access" in message:
                    _capabilities.set(capability, 403, settings.finnhub_cache_ttl_seconds)
                    raise ProviderError("finnhub", "capability_unavailable", "Finnhub capability is unavailable for the configured plan.", capability=capability, status_code=403)
                raise ProviderError("finnhub", "invalid_request", "Finnhub rejected the request.", capability=capability)
            _cache.set(key, payload, settings.finnhub_cache_ttl_seconds)
            return payload
        raise AssertionError("unreachable")

    async def economic_calendar(self, start_date: date, end_date: date, limit: int) -> list[EconomicEvent]:
        payload = await self._get("/calendar/economic", "economic_calendar", **{"from": start_date.isoformat(), "to": end_date.isoformat()})
        rows = payload.get("economicCalendar", []) if isinstance(payload, dict) else []
        events = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or not row.get("event"):
                continue
            events.append(EconomicEvent(
                event=str(row["event"]), country=row.get("country"), currency=row.get("currency"),
                scheduled_at=_datetime(row.get("time")), impact=row.get("impact"), period=row.get("period"),
                previous=row.get("prev", row.get("previous")), estimate=row.get("estimate"),
                actual=row.get("actual"), unit=row.get("unit"),
            ))
        return events

    async def market_news(self, category: str, limit: int) -> list[MarketNewsItem]:
        payload = await self._get("/news", "market_news", category=category)
        rows = payload if isinstance(payload, list) else []
        items = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or not row.get("headline") or not _datetime(row.get("datetime")):
                continue
            related = [item.strip() for item in str(row.get("related", "")).split(",") if item.strip()]
            summary = str(row["summary"])[:500] if row.get("summary") else None
            items.append(MarketNewsItem(
                id=str(row["id"]) if row.get("id") is not None else None,
                published_at=_datetime(row["datetime"]), headline=str(row["headline"])[:300],
                summary=summary, source_name=row.get("source"), related_symbols=related,
                url=row.get("url"),
            ))
        return items

    async def forex_candles(self, symbol: str, resolution: str, start_seconds: int, end_seconds: int) -> Any:
        return await self._get(
            "/forex/candle", "forex_candles", symbol=symbol, resolution=resolution,
            **{"from": start_seconds, "to": end_seconds},
        )

    async def symbol_search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        payload = await self._get("/search", "symbol_search", q=query)
        rows = payload.get("result", []) if isinstance(payload, dict) else []
        return [{
            "symbol": str(row.get("symbol", "")),
            "display_symbol": str(row.get("displaySymbol") or row.get("symbol") or ""),
            "description": str(row.get("description") or ""),
            "type": str(row.get("type") or "unknown").lower(),
            "source": "Finnhub",
        } for row in rows[:limit] if isinstance(row, dict) and row.get("symbol")]

    async def quote(self, symbol: str) -> dict[str, Any]:
        row = await self._get("/quote", "quote", symbol=symbol)
        if not isinstance(row, dict):
            row = {}
        return {
            "symbol": symbol, "price": row.get("c"), "change": row.get("d"),
            "change_percent": row.get("dp"), "high": row.get("h"), "low": row.get("l"),
            "open": row.get("o"), "previous_close": row.get("pc"), "timestamp": row.get("t"),
            "source": "Finnhub",
        }


def capability_status() -> dict[str, bool]:
    return {
        name: _capabilities.get(name) is None
        for name in ("economic_calendar", "market_news", "forex_candles", "symbol_search", "quote")
    }
