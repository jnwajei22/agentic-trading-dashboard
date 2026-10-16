from __future__ import annotations

import asyncio
from datetime import date, timedelta
from datetime import datetime, timezone
from typing import Any, Awaitable

from app.services.providers.errors import ProviderError
from app.services.providers.finnhub import FinnhubClient
from app.services.providers.fred import FredClient
from app.services.instruments import DEFAULT_FOREX_MAJORS, InstrumentMappingError, instrument_mapper


DEFAULT_SYMBOLS = tuple(f"forex:{symbol}" for symbol in DEFAULT_FOREX_MAJORS[:3])
DRIVER_LIMIT = 6
CURRENCY_DRIVERS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "USD": (("DFF", "U.S. Federal Funds Rate", "monetary_policy"),
            ("CPIAUCSL", "U.S. Consumer Price Index", "inflation"),
            ("UNRATE", "U.S. Unemployment Rate", "employment")),
    "EUR": (("ECBDFR", "ECB Deposit Facility Rate", "monetary_policy"),
            ("CP0000EZ19M086NEST", "Euro Area Consumer Prices", "inflation"),
            ("LRHUTTTTEZM156S", "Euro Area Unemployment Rate", "employment")),
    "GBP": (("IUDERB", "Bank of England Policy Rate", "monetary_policy"),
            ("GBRCPIALLMINMEI", "UK Consumer Price Index", "inflation"),
            ("LRHUTTTTGBM156S", "UK Unemployment Rate", "employment")),
    "JPY": (("IRSTCI01JPM156N", "Bank of Japan Policy Rate", "monetary_policy"),
            ("JPNCPIALLMINMEI", "Japan Consumer Price Index", "inflation"),
            ("LRHUTTTTJPM156S", "Japan Unemployment Rate", "employment")),
}
MACRO_SERIES = tuple(item[0] for item in CURRENCY_DRIVERS["USD"])


class MarketWorkspaceService:
    """Provider-agnostic market reads with partial-failure responses safe for browsers."""

    def __init__(self, finnhub: FinnhubClient | None = None, fred: FredClient | None = None) -> None:
        self.finnhub = finnhub or FinnhubClient()
        self.fred = fred or FredClient()

    @staticmethod
    def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "last_updated": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    async def _partial(provider: str, request: Awaitable[Any], fallback: Any) -> tuple[Any, dict | None]:
        try:
            return await request, None
        except ProviderError as exc:
            return fallback, exc.as_dict()
        except Exception:
            return fallback, {"provider": provider, "error": "upstream_failure",
                              "message": f"{provider} data is temporarily unavailable.", "retryable": True}

    async def search(self, query: str, limit: int = 25) -> dict[str, Any]:
        results, warning = await self._partial("Finnhub", self.finnhub.symbol_search(query, limit), [])
        canonical = [instrument for row in results if (instrument := instrument_mapper.from_finnhub(row))]
        if not canonical and query:
            try:
                canonical = [instrument_mapper.resolve(instrument_mapper.canonical_id("forex", query))]
            except InstrumentMappingError:
                canonical = []
        return self._stamp({"query": query, "results": [item.model_dump() for item in canonical[:limit]], "sources": ["Finnhub"],
                "warnings": [warning] if warning else []})

    async def overview(self, symbols: list[str] | None = None) -> dict[str, Any]:
        requested = (symbols or list(DEFAULT_SYMBOLS))[:12]
        mapped = []
        for canonical_id in requested:
            try:
                instrument = instrument_mapper.resolve(canonical_id)
            except InstrumentMappingError:
                continue
            provider_symbol = instrument.provider_symbols.get("finnhub")
            if provider_symbol:
                mapped.append((instrument, provider_symbol))
        rows = await asyncio.gather(*(self._partial("Finnhub", self.finnhub.quote(symbol), None)
                                      for _, symbol in mapped))
        quotes = [{**value, "canonical_id": instrument.canonical_id, "symbol": instrument.symbol}
                  for (instrument, _), (value, _) in zip(mapped, rows) if value]
        return self._stamp({"quotes": quotes, "sources": ["Finnhub"],
                "warnings": [warning for _, warning in rows if warning]})

    async def news(self, category: str = "general", limit: int = 20) -> dict[str, Any]:
        items, warning = await self._partial("Finnhub", self.finnhub.market_news(category, limit), [])
        return self._stamp({"items": [item.model_dump(mode="json") for item in items], "sources": ["Finnhub"],
                "warnings": [warning] if warning else []})

    @staticmethod
    def _currencies(instrument: Any) -> tuple[str, ...]:
        if instrument.asset_class != "forex" or "/" not in instrument.symbol:
            return ()
        base, quote = instrument.symbol.split("/", 1)
        return base.upper(), quote.upper()

    async def _driver(self, currency: str, config: tuple[str, str, str]) -> dict[str, Any]:
        series_id, label, category = config
        metadata, observations = await asyncio.gather(
            self.fred.series_metadata(series_id),
            self.fred.observations(series_id, observation_start=date.today() - timedelta(days=550), limit=550),
        )
        values = [item for item in observations if item.value is not None]
        if not values:
            raise ProviderError("fred", "upstream_failure", f"No observations were returned for {series_id}.")
        latest = values[-1]
        return {"series_id": series_id, "label": label, "value": latest.value,
                "unit": metadata.units, "observation_date": latest.date.isoformat(), "source": "FRED",
                "category": category, "applies_to": currency,
                "stale": (date.today() - latest.date).days > 120,
                "previous": values[-2].value if len(values) > 1 else None,
                "trend": [item.value for item in values[-5:]]}

    async def _drivers(self, instrument: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected = [(currency, config) for currency in self._currencies(instrument)
                    for config in CURRENCY_DRIVERS.get(currency, ())][:DRIVER_LIMIT]
        rows = await asyncio.gather(*(self._partial("FRED", self._driver(currency, config), None)
                                      for currency, config in selected))
        return [value for value, _ in rows if value], [warning for _, warning in rows if warning]

    async def calendar(self, start: date, end: date, limit: int = 50,
                       canonical_id: str | None = None) -> dict[str, Any]:
        items, warning = await self._partial("Finnhub", self.finnhub.economic_calendar(start, end, limit), [])
        instrument = instrument_mapper.resolve(canonical_id) if canonical_id else None
        currencies = self._currencies(instrument) if instrument else ()
        drivers, driver_warnings = await self._drivers(instrument) if instrument else ([], [])
        normalized = []
        for item in items:
            row = item.model_dump(mode="json")
            currency = str(row.get("currency") or "").upper()
            title = str(row.get("event") or "")
            lower = title.casefold()
            category = ("inflation" if any(key in lower for key in ("inflation", "cpi", "prices")) else
                        "employment" if any(key in lower for key in ("employment", "unemployment", "jobs", "payroll")) else
                        "monetary_policy" if any(key in lower for key in ("rate", "central bank", "fomc", "ecb", "boe", "boj")) else None)
            related = bool(instrument and currency in currencies)
            context = [{key: driver[key] for key in ("series_id", "label", "value", "previous", "trend", "unit", "observation_date", "source", "stale")}
                       for driver in drivers if driver["applies_to"] == currency and driver["category"] == category]
            normalized.append({"title": title, "country": row.get("country"), "currency": currency or None,
                "scheduled_time": row.get("scheduled_at"), "impact": row.get("impact"),
                "previous": row.get("previous"), "estimate": row.get("estimate"), "actual": row.get("actual"),
                "unit": row.get("unit"), "source": "Finnhub", "related_canonical_instruments": [canonical_id] if related else [],
                "associated_fred_series": context, "relevant": related,
                "relevance_label": f"Relevant to {currency} side of {instrument.symbol}" if related else None})
        normalized.sort(key=lambda row: (not row["relevant"], str(row.get("scheduled_time") or "")))
        sources = ["Finnhub"] + (["FRED"] if drivers else [])
        return self._stamp({"items": normalized, "sources": sources,
                "warnings": ([warning] if warning else []) + driver_warnings})

    async def macro(self) -> dict[str, Any]:
        async def series(series_id: str) -> dict[str, Any]:
            metadata, observations = await asyncio.gather(
                self.fred.series_metadata(series_id),
                self.fred.observations(series_id, observation_start=date.today() - timedelta(days=550), limit=550),
            )
            latest = next((item for item in reversed(observations) if item.value is not None), None)
            return {"series": metadata.model_dump(mode="json"),
                    "latest": latest.model_dump(mode="json") if latest else None, "source": "FRED"}

        rows = await asyncio.gather(*(self._partial("FRED", series(series_id), None)
                                      for series_id in MACRO_SERIES))
        return self._stamp({"indicators": [value for value, _ in rows if value], "sources": ["FRED"],
                "warnings": [warning for _, warning in rows if warning]})

    async def symbol(self, canonical_id: str) -> dict[str, Any]:
        instrument = instrument_mapper.resolve(canonical_id)
        finnhub_symbol = instrument.provider_symbols.get("finnhub")
        quote, warning = await self._partial("Finnhub", self.finnhub.quote(finnhub_symbol), None) if finnhub_symbol else (None, {
            "provider": "Finnhub", "error": "capability_unavailable", "message": "This instrument is unsupported.", "retryable": False})
        return self._stamp({"instrument": instrument.model_dump(), "quote": quote,
                "tradingview_symbol": instrument.provider_symbols.get("tradingview"),
                "sources": ["Finnhub", "TradingView"], "warnings": [warning] if warning else []})

    async def summary(self, canonical_id: str, execution_quote: dict[str, Any] | None = None,
                      tradability: dict[str, Any] | None = None) -> dict[str, Any]:
        instrument = instrument_mapper.resolve(canonical_id)
        symbol = instrument.provider_symbols.get("finnhub")
        updated_at = datetime.now(timezone.utc).isoformat()
        drivers, driver_warnings = await self._drivers(instrument)
        quote, warning = await self._partial("Finnhub", self.finnhub.quote(symbol), None) if symbol else (None, {
            "provider": "Finnhub", "error": "capability_unavailable", "message": "Quote is unsupported.", "retryable": False})
        provider = "finnhub"
        if (not quote or quote.get("price") is None) and execution_quote and execution_quote.get("price") is not None:
            quote, provider = execution_quote, str(execution_quote.get("source") or "execution_provider").lower()
        normalized = None
        stale = False
        if quote and quote.get("price") is not None:
            normalized = {key: quote.get(key) for key in ("price", "change", "change_percent", "open", "high", "low", "previous_close", "bid", "ask")}
            stamp = quote.get("timestamp")
            normalized["timestamp"] = (datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                                       if isinstance(stamp, (int, float)) else stamp)
            normalized["source"] = provider
            normalized["authoritative_for_execution"] = provider != "finnhub"
            stale = bool(isinstance(stamp, (int, float)) and datetime.now(timezone.utc).timestamp() - stamp > 300)
        quote_status = "stale" if stale else "available" if normalized else (
            "unsupported" if warning and warning.get("error") in {"capability_unavailable", "invalid_request"} else "unavailable")
        sources = [{"provider": provider if normalized else "finnhub", "status": quote_status,
                    "updated_at": normalized.get("timestamp") if normalized else updated_at,
                    "message": None if normalized else (warning.get("message") if warning else "No quote data was returned.")}]
        if drivers or driver_warnings:
            sources.append({"provider": "fred", "status": "available" if drivers else "unavailable",
                            "updated_at": max((row["observation_date"] for row in drivers), default=None),
                            "message": "Some economic drivers are unavailable." if driver_warnings else None})
        return {"instrument": instrument.model_dump(), "quote": normalized,
            "market_status": {"state": "unknown", "label": "Market status not provided by this quote source"},
            "drivers": drivers, "tradability": tradability, "sources": sources,
            "partial": stale or normalized is None or bool(driver_warnings)}

    async def aclose(self) -> None:
        await asyncio.gather(self.finnhub.aclose(), self.fred.aclose())
