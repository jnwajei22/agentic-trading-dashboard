import pytest

from app.services import desk_tools as tools


@pytest.mark.asyncio
async def test_public_provider_errors_do_not_break_tradelocker_bundle(monkeypatch):
    async def market(*args, **kwargs):
        return {"source": "tradelocker", "candles": [{"close": 1.1}], "complete": True}

    async def calendar(*args, **kwargs):
        return {"status": "error", "provider": "finnhub", "message": "calendar unavailable"}

    async def news(*args, **kwargs):
        return {"status": "error", "provider": "finnhub", "message": "news unavailable"}

    async def macro(*args, **kwargs):
        return {"status": "error", "provider": "fred", "message": "macro unavailable"}

    monkeypatch.setattr(tools, "get_market_candles", market)
    monkeypatch.setattr(tools, "get_economic_calendar", calendar)
    monkeypatch.setattr(tools, "get_market_news", news)
    monkeypatch.setattr(tools, "get_macro_series", macro)
    result = await tools.get_forex_research_bundle(
        "EURUSD", "1H", include_quote=False, include_account_exposure=False,
        macro_series_ids=["TEST"],
    )
    assert result["market"]["source"] == "tradelocker"
    assert len(result["warnings"]) == 3
    assert result["sources"]["execution"] == "tradelocker"
    assert result["economic_calendar"]["provider"] == "finnhub"
