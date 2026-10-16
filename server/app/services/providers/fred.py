from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from typing import Any

import httpx

from app.config.settings import settings
from app.models.providers import MacroObservation, MacroReleaseDate, MacroSeriesMetadata
from app.services.providers.cache import TTLCache
from app.services.providers.errors import ProviderError


_cache = TTLCache()


def _key(path: str, params: dict[str, Any]) -> str:
    public = {key: value for key, value in params.items() if key != "api_key"}
    return hashlib.sha256(json.dumps([path, public], sort_keys=True, default=str).encode()).hexdigest()


def _metadata(row: dict[str, Any]) -> MacroSeriesMetadata:
    last_updated = row.get("last_updated")
    return MacroSeriesMetadata(
        series_id=str(row.get("id", row.get("series_id"))), title=str(row.get("title", "")),
        units=row.get("units"), frequency=row.get("frequency"), seasonal_adjustment=row.get("seasonal_adjustment"),
        observation_start=date.fromisoformat(row["observation_start"]) if row.get("observation_start") else None,
        observation_end=date.fromisoformat(row["observation_end"]) if row.get("observation_end") else None,
        last_updated=datetime.fromisoformat(last_updated.replace("Z", "+00:00")) if last_updated else None,
    )


class FredClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=settings.fred_base_url.rstrip("/"), timeout=settings.fred_timeout_seconds, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _validate(self) -> None:
        if not settings.fred_enabled:
            raise ProviderError("fred", "not_configured", "FRED is disabled.")
        if not settings.fred_api_key:
            raise ProviderError("fred", "not_configured", "FRED API key is not configured.")

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        self._validate()
        request_params = {**params, "api_key": settings.fred_api_key, "file_type": "json"}
        key = _key(path, request_params)
        cached = _cache.get(key)
        if cached is not None:
            return cached
        for attempt in range(settings.fred_max_retries + 1):
            try:
                response = await self._http.get(path, params=request_params)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < settings.fred_max_retries:
                    await asyncio.sleep(0.1 * 2**attempt)
                    continue
                category = "upstream_timeout" if isinstance(exc, httpx.TimeoutException) else "upstream_failure"
                raise ProviderError("fred", category, "FRED request failed temporarily.", retryable=True) from None
            if response.status_code == 429:
                raise ProviderError("fred", "rate_limited", "FRED rate limit was reached.", retryable=True, status_code=429)
            if response.status_code in {401, 403}:
                raise ProviderError("fred", "authentication_failed", "FRED authentication failed.", status_code=response.status_code)
            if 500 <= response.status_code < 600:
                if attempt < settings.fred_max_retries:
                    await asyncio.sleep(0.1 * 2**attempt)
                    continue
                raise ProviderError("fred", "upstream_failure", "FRED is temporarily unavailable.", retryable=True, status_code=response.status_code)
            if response.status_code >= 400:
                raise ProviderError("fred", "invalid_request", "FRED rejected the request.", status_code=response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError("fred", "upstream_failure", "FRED returned invalid JSON.", retryable=True) from None
            _cache.set(key, payload, settings.fred_cache_ttl_seconds)
            return payload
        raise AssertionError("unreachable")

    async def search_series(self, query: str, limit: int = 25) -> list[MacroSeriesMetadata]:
        payload = await self._get("/series/search", search_text=query, limit=min(limit, 1000), offset=0)
        return [_metadata(row) for row in payload.get("seriess", []) if isinstance(row, dict)]

    async def series_metadata(self, series_id: str) -> MacroSeriesMetadata:
        payload = await self._get("/series", series_id=series_id)
        rows = payload.get("seriess", [])
        if not rows:
            raise ProviderError("fred", "invalid_request", "FRED series was not found.")
        return _metadata(rows[0])

    async def observations(
        self, series_id: str, *, observation_start: date | None = None,
        observation_end: date | None = None, realtime_start: date | None = None,
        realtime_end: date | None = None, vintage_dates: list[date] | None = None,
        limit: int = 1000,
    ) -> list[MacroObservation]:
        params: dict[str, Any] = {"series_id": series_id, "sort_order": "asc"}
        for name, value in (("observation_start", observation_start), ("observation_end", observation_end), ("realtime_start", realtime_start), ("realtime_end", realtime_end)):
            if value:
                params[name] = value.isoformat()
        if vintage_dates:
            params["vintage_dates"] = ",".join(value.isoformat() for value in vintage_dates)
        result = []
        offset = 0
        while len(result) < limit:
            page_limit = min(1000, limit - len(result))
            payload = await self._get("/series/observations", **params, limit=page_limit, offset=offset)
            rows = payload.get("observations", [])
            for row in rows:
                value = row.get("value")
                result.append(MacroObservation(
                    series_id=series_id, date=date.fromisoformat(row["date"]),
                    value=None if value in {None, "."} else float(value),
                    realtime_start=date.fromisoformat(row["realtime_start"]) if row.get("realtime_start") else None,
                    realtime_end=date.fromisoformat(row["realtime_end"]) if row.get("realtime_end") else None,
                ))
            offset += len(rows)
            total = int(payload.get("count", offset))
            if not rows or offset >= total:
                break
        return result

    async def release_dates(self, start: date | None, end: date | None, limit: int = 100) -> list[MacroReleaseDate]:
        results: list[MacroReleaseDate] = []
        offset = 0
        for _ in range(5):
            payload = await self._get(
                "/releases/dates", limit=1000, offset=offset,
                sort_order="desc", order_by="release_date",
                include_release_dates_with_no_data="true",
            )
            rows = payload.get("release_dates", [])
            page = [
                MacroReleaseDate(release_id=int(row["release_id"]), release_name=str(row["release_name"]), date=date.fromisoformat(row["date"]))
                for row in rows if isinstance(row, dict)
            ]
            results.extend(
                item for item in page
                if (start is None or item.date >= start) and (end is None or item.date <= end)
            )
            offset += len(rows)
            total = int(payload.get("count", offset))
            if len(results) >= limit or not rows or offset >= total or (start and page and page[-1].date < start):
                break
        return results[:limit]
