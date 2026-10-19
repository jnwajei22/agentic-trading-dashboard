from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from app.api.routes import platform
from app.brokers.tradelocker.client import TradeLockerError
from app.models.market import Candle


@pytest.fixture
def feed(monkeypatch):
    calls = []
    context = {"base_url":"https://broker.test", "username":"test", "password":"secret", "server":"demo", "account_id":"1", "account_number":"1", "account_ref":"account_owned"}
    class Repo:
        def account_connection_context(self, owner, alias):
            calls.append((owner, alias))
            return context if (owner, alias) == ("owner", "mine") else None
    class Client:
        fail_quote = False
        fail_history = False
        def __init__(self, **kwargs):
            assert kwargs["cache_user_id"] == "owner"
            assert kwargs["cache_account_record_id"] == "account_owned"
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_quote(self, symbol):
            calls.append(("quote", symbol))
            if self.fail_quote: raise TradeLockerError("get_quote", "private upstream detail")
            return {"d":{"bp":1.1,"ap":1.2}}
        async def get_candles(self, symbol, timeframe, lookback, **kwargs):
            calls.append(("candles", symbol, timeframe, lookback))
            if self.fail_history: raise TradeLockerError("get_candles", "private upstream detail")
            return SimpleNamespace(candles=[Candle(timestamp=1_792_000_000_000,open=1.1,high=1.2,low=1,close=1.15)],cache_hit=False,cache_fresh=False)
    monkeypatch.setattr(platform, "repository", Repo)
    monkeypatch.setattr(platform, "TradeLockerClient", Client)
    return calls, Client


@pytest.mark.asyncio
async def test_broker_chart_preserves_candles_and_does_not_invent_quote_time(feed):
    calls, _ = feed
    result = await platform.broker_market_chart("forex:EUR/USD", "mine", claims={"sub":"owner"})
    assert calls[0] == ("owner", "mine")
    assert result["quote"]["price"] == pytest.approx(1.15)
    assert result["quote"]["timestamp"] is None
    assert result["candles"][0]["timestamp"] == 1_792_000_000_000
    assert result["candles"][0]["close"] == 1.15
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_quote_refresh_does_not_refetch_candles(feed):
    calls, _ = feed
    result = await platform.broker_market_chart("forex:EUR/USD", "mine", history=False, claims={"sub":"owner"})
    assert result["candles"] == []
    assert not any(call[0] == "candles" for call in calls)


@pytest.mark.asyncio
async def test_chart_rejects_another_owners_account_before_upstream_access(feed):
    calls, _ = feed
    with pytest.raises(HTTPException) as exc:
        await platform.broker_market_chart("forex:EUR/USD", "mine", claims={"sub":"stranger"})
    assert exc.value.status_code == 404
    assert calls == [("stranger", "mine")]


@pytest.mark.asyncio
async def test_history_failure_preserves_real_quote_with_safe_warning(feed):
    _, client = feed
    client.fail_history = True
    result = await platform.broker_market_chart("forex:EUR/USD", "mine", claims={"sub":"owner"})
    assert result["quote"]
    assert result["candles"] == []
    assert result["warnings"]
    assert "private upstream detail" not in str(result)


@pytest.mark.asyncio
async def test_total_feed_failure_returns_error_instead_of_fake_chart(feed):
    _, client = feed
    client.fail_quote = client.fail_history = True
    with pytest.raises(HTTPException) as exc:
        await platform.broker_market_chart("forex:EUR/USD", "mine", claims={"sub":"owner"})
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_partial_forex_search_works_without_external_feed():
    from app.services.market_workspace import MarketWorkspaceService
    from app.services.providers.errors import ProviderError
    class Disabled:
        async def symbol_search(self, query, limit):
            raise ProviderError("finnhub", "not_configured", "Finnhub is disabled.")
        async def aclose(self): pass
    service = MarketWorkspaceService(Disabled())
    try:
        result = await service.search("eur")
        assert [row["canonical_id"] for row in result["results"]] == ["forex:EUR/USD"]
        assert result["warnings"][0]["error"] == "not_configured"
    finally:
        await service.aclose()
