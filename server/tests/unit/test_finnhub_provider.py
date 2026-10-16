from datetime import date

import httpx
import pytest

from app.config.settings import settings
from app.services.providers import finnhub
from app.services.providers.errors import ProviderError


@pytest.fixture(autouse=True)
def clear_cache():
    finnhub._cache.clear()
    finnhub._capabilities.clear()


@pytest.mark.asyncio
async def test_disabled_and_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_enabled", False)
    client = finnhub.FinnhubClient()
    with pytest.raises(ProviderError) as caught:
        await client.market_news("forex", 10)
    assert caught.value.response.error == "not_configured"
    await client.aclose()

    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", None)
    client = finnhub.FinnhubClient()
    with pytest.raises(ProviderError) as caught:
        await client.market_news("forex", 10)
    assert "key" in caught.value.response.message.lower()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [(403, "capability_unavailable"), (429, "rate_limited")])
async def test_permission_and_rate_limit(monkeypatch, status, error):
    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", "secret-key")
    client = finnhub.FinnhubClient(httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "premium access"})))
    with pytest.raises(ProviderError) as caught:
        await client.market_news("forex", 10)
    assert caught.value.response.error == error
    assert "secret-key" not in str(caught.value.as_dict())
    await client.aclose()


@pytest.mark.asyncio
async def test_temporary_5xx_retries_and_normalizes_calendar(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", "secret-key")
    monkeypatch.setattr(settings, "finnhub_max_retries", 2)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 2:
            return httpx.Response(503, json={})
        assert request.url.params["token"] == "secret-key"
        return httpx.Response(200, json={"economicCalendar": [{
            "event": "Policy Rate", "country": "US", "time": "2026-07-14 12:00:00",
            "impact": "high", "prev": 5.0, "estimate": None, "actual": None, "unit": "%",
        }]})

    client = finnhub.FinnhubClient(httpx.MockTransport(handler))
    events = await client.economic_calendar(date(2026, 7, 14), date(2026, 7, 14), 10)
    assert calls == 2 and events[0].event == "Policy Rate"
    assert events[0].estimate is None and events[0].scheduled_at.tzinfo is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_news_is_bounded_and_concise(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", "key")
    rows = [{"id": index, "datetime": 1784000000 + index, "headline": f"FX {index}", "summary": "x" * 900, "source": "Wire", "related": "EURUSD", "url": "https://example.test"} for index in range(5)]
    client = finnhub.FinnhubClient(httpx.MockTransport(lambda request: httpx.Response(200, json=rows)))
    items = await client.market_news("forex", 2)
    assert len(items) == 2 and len(items[0].summary) == 500


@pytest.mark.asyncio
async def test_search_and_quote_are_normalized(monkeypatch):
    monkeypatch.setattr(settings,"finnhub_enabled",True);monkeypatch.setattr(settings,"finnhub_api_key","key")
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200,json={"result":[{"symbol":"AAPL","displaySymbol":"AAPL","description":"Apple","type":"Common Stock"}]})
        return httpx.Response(200,json={"c":200,"dp":1.5,"t":1784000000})
    client=finnhub.FinnhubClient(httpx.MockTransport(handler))
    assert (await client.symbol_search("apple"))[0]["source"]=="Finnhub"
    assert (await client.quote("AAPL"))["price"]==200
    await client.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_key_is_redacted(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", "timeout-secret")
    monkeypatch.setattr(settings, "finnhub_max_retries", 1)

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = finnhub.FinnhubClient(httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as caught:
        await client.market_news("forex", 1)
    assert caught.value.response.error == "upstream_timeout"
    assert caught.value.response.retryable
    assert "timeout-secret" not in str(caught.value.as_dict())
    await client.aclose()
