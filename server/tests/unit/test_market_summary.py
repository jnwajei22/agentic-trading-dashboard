from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.providers import EconomicEvent, MacroObservation, MacroSeriesMetadata
from app.services.market_workspace import DRIVER_LIMIT, MarketWorkspaceService
from app.services.providers.errors import ProviderError


class Finnhub:
    async def quote(self, symbol):
        assert symbol == "OANDA:EUR_USD"
        return {"symbol": symbol, "price": 1.1234, "change": .0012, "change_percent": .11,
                "open": 1.121, "high": 1.125, "low": 1.1195, "previous_close": 1.1222,
                "timestamp": datetime.now(timezone.utc).timestamp()}

    async def economic_calendar(self, start, end, limit):
        return [
            EconomicEvent(event="Euro Area CPI", country="EU", currency="EUR", impact="high"),
            EconomicEvent(event="US Payrolls", country="US", currency="USD", impact="high"),
            EconomicEvent(event="Canada GDP", country="CA", currency="CAD", impact="medium"),
        ]

    async def aclose(self): pass


class Unavailable(Finnhub):
    async def quote(self, symbol):
        raise ProviderError("finnhub", "upstream_failure", "Unavailable", retryable=True)


class Fred:
    async def series_metadata(self, series_id):
        return MacroSeriesMetadata(series_id=series_id, title=series_id, units="Percent")

    async def observations(self, series_id, **kwargs):
        return [
            MacroObservation(series_id=series_id, date=date.today() - timedelta(days=60), value=2.0),
            MacroObservation(series_id=series_id, date=date.today() - timedelta(days=30), value=2.5),
        ]

    async def aclose(self): pass


@pytest.mark.asyncio
async def test_summary_selects_both_forex_sides_and_excludes_irrelevant_series():
    result = await MarketWorkspaceService(Finnhub(), Fred()).summary("forex:EUR/USD")
    assert result["quote"]["price"] == 1.1234
    assert {item["applies_to"] for item in result["drivers"]} == {"EUR", "USD"}
    assert len(result["drivers"]) <= DRIVER_LIMIT
    assert all(item["series_id"] not in {"JPNCPIALLMINMEI", "GBRCPIALLMINMEI"} for item in result["drivers"])
    assert result["sources"][1]["provider"] == "fred"


@pytest.mark.asyncio
async def test_quote_failure_preserves_fred_drivers_and_never_invents_zero():
    result = await MarketWorkspaceService(Unavailable(), Fred()).summary("forex:EUR/USD")
    assert result["quote"] is None
    assert result["drivers"]
    assert result["partial"] is True


@pytest.mark.asyncio
async def test_approved_execution_quote_is_used_as_fallback():
    fallback = {"price": 1.2345, "bid": 1.2344, "ask": 1.2346, "source": "TradeLocker"}
    result = await MarketWorkspaceService(Unavailable(), Fred()).summary("forex:EUR/USD", fallback)
    assert result["quote"]["price"] == 1.2345
    assert result["quote"]["source"] == "tradelocker"
    assert result["quote"]["authoritative_for_execution"] is True


@pytest.mark.asyncio
async def test_calendar_prioritizes_pair_currencies_and_enriches_matching_events():
    result = await MarketWorkspaceService(Finnhub(), Fred()).calendar(
        date.today(), date.today() + timedelta(days=7), canonical_id="forex:EUR/USD")
    assert [item["currency"] for item in result["items"][:2]] == ["EUR", "USD"]
    assert result["items"][0]["associated_fred_series"]
    assert result["items"][0]["relevance_label"] == "Relevant to EUR side of EUR/USD"
    unrelated = next(item for item in result["items"] if item["currency"] == "CAD")
    assert unrelated["relevant"] is False
    assert unrelated["related_canonical_instruments"] == []
