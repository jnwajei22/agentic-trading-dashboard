from datetime import datetime, timedelta, timezone

import pytest

from app.auth.identity import reset_current_claims, set_current_claims
from app.services import desk_tools as tools
from app.models.providers import MarketCandle, MarketSeries
from app.services.market_data.series_cache import market_series_cache


def series(*, complete: bool = True, candles: bool = True) -> MarketSeries:
    start = datetime(2026, 7, 10, tzinfo=timezone.utc)
    points = [
        MarketCandle(timestamp=start + timedelta(hours=index), open=1.1, high=1.2, low=1.0, close=1.15, volume=10)
        for index in range(2)
    ] if candles else []
    return MarketSeries(
        symbol="EUR/USD", normalized_symbol="EURUSD", timeframe="1H", source="tradelocker",
        provider_symbol="77", requested_start=start, requested_end=start + timedelta(hours=1),
        actual_start=points[0].timestamp if points else None, actual_end=points[-1].timestamp if points else None,
        candles_returned=len(points), complete=complete, warning=None if complete else "partial",
        retrieved_at=start, candles=points,
    )


@pytest.fixture(autouse=True)
def clear_cache():
    market_series_cache.clear()
    yield
    market_series_cache.clear()


async def render(owner: str, **kwargs):
    token = set_current_claims({"sub": owner})
    try:
        return await tools.render_market_chart(**kwargs)
    finally:
        reset_current_claims(token)


@pytest.mark.asyncio
async def test_native_chart_payload_preserves_incomplete_metadata():
    entry = market_series_cache.put("user-a", series(complete=False))
    result = await render("user-a", series_id=entry.series_id)
    assert result["status"] == "ready"
    assert "candles" not in result
    assert len(result["chart"]["candles"]) == 2
    assert result["chart"]["complete"] is False
    assert result["chart"]["warning"] == "partial"


@pytest.mark.asyncio
async def test_other_user_receives_access_denied():
    entry = market_series_cache.put("user-a", series())
    result = await render("user-b", series_id=entry.series_id)
    assert result["error"] == "series_access_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"horizontal_overlays": [{"type": "horizontal_line", "label": "<b>x</b>", "price": 1.1}]}, "invalid_overlay"),
        ({"line_overlays": [{"type": "line_series", "label": "EMA", "points": [{"timestamp": "nope", "value": 1.1}]}]}, "invalid_overlay"),
        ({"markers": [{"timestamp": "2026-07-10T00:00:00Z", "position": "side", "shape": "circle", "label": "x"}]}, "invalid_marker"),
    ],
)
async def test_malformed_annotations_are_normalized(arguments, error):
    entry = market_series_cache.put("user-a", series())
    result = await render("user-a", series_id=entry.series_id, **arguments)
    assert result["status"] == "error" and result["error"] == error
    assert result["details"]


@pytest.mark.asyncio
async def test_empty_payload_is_normalized():
    entry = market_series_cache.put("user-a", series(candles=False))
    result = await render("user-a", series_id=entry.series_id)
    assert result["error"] == "chart_payload_empty"


@pytest.mark.asyncio
async def test_expired_series_is_normalized(monkeypatch):
    monkeypatch.setattr(market_series_cache, "get", lambda owner, key: ("expired", None))
    result = await render("user-a", series_id="abcdefghijklmnop")
    assert result["error"] == "series_expired"


@pytest.mark.asyncio
async def test_get_market_candles_adds_user_owned_series_id(monkeypatch):
    expected = series()
    monkeypatch.setattr(tools, "get_market_series", lambda **kwargs: _async_value(expected))
    monkeypatch.setattr(tools, "_missing_user_connection", lambda: None)
    context = type("Context", (), {"base_url":"https://demo.test","username":"u","password":"p","server":"s","account_id":"a","account_number":"1"})()
    monkeypatch.setattr(tools, "BrokerAccountResolver", lambda: type("Resolver", (), {"resolve": lambda self, *args, **kwargs: context})())
    token = set_current_claims({"sub": "user-a"})
    try:
        result = await tools.get_market_candles("EURUSD", "1H")
    finally:
        reset_current_claims(token)
    assert result["series_id"]
    assert result["series_expires_at"]
    assert result["client_usage"]["render_tool"] == "render_market_chart"
    assert result["candles"]


async def _async_value(value):
    return value
