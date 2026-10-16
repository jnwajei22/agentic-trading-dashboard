from datetime import date

import httpx
import pytest

from app.config.settings import settings
from app.services.providers import fred
from app.services.providers.errors import ProviderError


@pytest.fixture(autouse=True)
def clear_cache():
    fred._cache.clear()


@pytest.mark.asyncio
async def test_disabled_missing_key_and_redaction(monkeypatch):
    monkeypatch.setattr(settings, "fred_enabled", False)
    client = fred.FredClient()
    with pytest.raises(ProviderError) as caught:
        await client.search_series("rates")
    assert caught.value.response.error == "not_configured"
    await client.aclose()

    monkeypatch.setattr(settings, "fred_enabled", True)
    monkeypatch.setattr(settings, "fred_api_key", "super-secret")
    client = fred.FredClient(httpx.MockTransport(lambda request: httpx.Response(400, json={"error_message": "bad"})))
    with pytest.raises(ProviderError) as caught:
        await client.search_series("rates")
    assert "super-secret" not in str(caught.value.as_dict())
    await client.aclose()


@pytest.mark.asyncio
async def test_search_metadata_observations_missing_values_and_vintage_params(monkeypatch):
    monkeypatch.setattr(settings, "fred_enabled", True)
    monkeypatch.setattr(settings, "fred_api_key", "key")

    def handler(request):
        path = request.url.path
        if path.endswith("/series/search"):
            return httpx.Response(200, json={"seriess": [{"id": "TEST", "title": "Test", "units": "Percent", "frequency": "Monthly", "seasonal_adjustment": "Not Seasonally Adjusted", "observation_start": "2020-01-01", "observation_end": "2026-01-01", "last_updated": "2026-01-02T00:00:00+00:00"}]})
        if path.endswith("/series/observations"):
            assert request.url.params["realtime_start"] == "2025-01-01"
            assert request.url.params["vintage_dates"] == "2025-01-01,2026-01-01"
            return httpx.Response(200, json={"observations": [
                {"date": "2025-01-01", "value": ".", "realtime_start": "2025-01-01", "realtime_end": "2025-12-31"},
                {"date": "2025-02-01", "value": "2.5", "realtime_start": "2025-01-01", "realtime_end": "2025-12-31"},
            ]})
        raise AssertionError(path)

    client = fred.FredClient(httpx.MockTransport(handler))
    results = await client.search_series("test")
    observations = await client.observations("TEST", realtime_start=date(2025, 1, 1), vintage_dates=[date(2025, 1, 1), date(2026, 1, 1)])
    assert results[0].series_id == "TEST"
    assert observations[0].value is None and observations[1].value == 2.5
    assert observations[0].realtime_end == date(2025, 12, 31)
    await client.aclose()


@pytest.mark.asyncio
async def test_metadata_and_release_dates(monkeypatch):
    monkeypatch.setattr(settings, "fred_enabled", True)
    monkeypatch.setattr(settings, "fred_api_key", "key")

    def handler(request):
        if request.url.path.endswith("/releases/dates"):
            return httpx.Response(200, json={"release_dates": [{"release_id": 10, "release_name": "Employment", "date": "2026-07-14"}]})
        return httpx.Response(200, json={"seriess": [{"id": "TEST", "title": "Test"}]})

    client = fred.FredClient(httpx.MockTransport(handler))
    metadata = await client.series_metadata("TEST")
    releases = await client.release_dates(None, None, 10)
    assert metadata.title == "Test" and releases[0].release_id == 10
    await client.aclose()


@pytest.mark.asyncio
async def test_observation_pagination(monkeypatch):
    monkeypatch.setattr(settings, "fred_enabled", True)
    monkeypatch.setattr(settings, "fred_api_key", "key")
    calls = []

    def handler(request):
        offset = int(request.url.params["offset"])
        calls.append(offset)
        rows = [{"date": f"2026-01-{offset + index + 1:02d}", "value": "1"} for index in range(2)]
        return httpx.Response(200, json={"count": 4, "observations": rows})

    client = fred.FredClient(httpx.MockTransport(handler))
    values = await client.observations("TEST", limit=4)
    assert len(values) == 4 and calls == [0, 2]
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_and_temporary_failure_contract(monkeypatch):
    monkeypatch.setattr(settings, "fred_enabled", True)
    monkeypatch.setattr(settings, "fred_api_key", "hidden-key")
    monkeypatch.setattr(settings, "fred_max_retries", 1)

    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = fred.FredClient(httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as caught:
        await client.search_series("test")
    assert caught.value.response.error == "upstream_timeout"
    assert caught.value.response.retryable
    assert "hidden-key" not in str(caught.value.as_dict())
    await client.aclose()
