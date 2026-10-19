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
async def test_live_refresh_fetches_a_small_forming_candle_tail(feed):
    calls, _ = feed
    result = await platform.broker_market_chart("forex:EUR/USD", "mine", history=False, claims={"sub":"owner"})
    assert result["candles"]
    assert ("candles", "EURUSD", "5m", 3) in calls


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


@pytest.mark.asyncio
async def test_stream_reuses_one_client_and_closes_on_disconnect(feed, monkeypatch):
    import json
    calls, client = feed
    lifecycle = []
    original_init = client.__init__
    def init(self, **kwargs):
        lifecycle.append("created")
        original_init(self, **kwargs)
    async def close(self, *args): lifecycle.append("closed")
    async def no_sleep(seconds): pass
    monkeypatch.setattr(client, "__init__", init)
    monkeypatch.setattr(client, "__aexit__", close)
    monkeypatch.setattr(platform.asyncio, "sleep", no_sleep)
    class Request:
        disconnected = False
        async def is_disconnected(self): return self.disconnected
    request = Request()
    response = await platform.broker_market_chart_stream(request, "forex:EUR/USD", "mine", claims={"sub":"owner"})
    events = response.body_iterator
    assert "connected" in await anext(events)
    first = await anext(events)
    second = await anext(events)
    payload = json.loads(second.split("data: ", 1)[1])
    assert payload["transport"] == "sampled_sse"
    assert payload["quote"]["timestamp"] is None
    assert "secret" not in first + second
    assert ("candles", "EURUSD", "5m", 120) in calls
    assert ("candles", "EURUSD", "5m", 3) in calls
    assert lifecycle == ["created"]
    request.disconnected = True
    assert "rotate" in await anext(events)
    assert lifecycle == ["created", "closed"]
    await events.aclose()


@pytest.mark.asyncio
async def test_stream_checks_ownership_before_streaming_headers(feed):
    calls, _ = feed
    with pytest.raises(HTTPException) as exc:
        await platform.broker_market_chart_stream(None, "forex:EUR/USD", "mine", claims={"sub":"stranger"})
    assert exc.value.status_code == 404
    assert calls == [("stranger", "mine")]


@pytest.mark.asyncio
async def test_stream_failure_is_sanitized_and_cancellation_closes_client(feed, monkeypatch):
    _, client = feed
    client.fail_quote = client.fail_history = True
    lifecycle = []
    async def close(self, *args): lifecycle.append("closed")
    monkeypatch.setattr(client, "__aexit__", close)
    class Request:
        async def is_disconnected(self): return False
    events = platform._broker_chart_events(Request(), {}, "owner", "forex:EUR/USD", "EURUSD", "1m")
    # Supply the same scoped client constructor without requiring real credentials.
    monkeypatch.setattr(platform, "_chart_client", lambda context, owner: client(cache_user_id="owner", cache_account_record_id="account_owned"))
    await anext(events)
    error = await anext(events)
    assert "feed_error" in error
    assert "private upstream" not in error
    await events.aclose()
    assert lifecycle == ["closed"]
