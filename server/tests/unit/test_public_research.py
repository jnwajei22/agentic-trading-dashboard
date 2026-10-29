from datetime import date

import httpx
import pytest

from app.services.providers.public_research import PublicResearchClient, _cache, parse_calendar, parse_news
from app.services.market_workspace import MarketWorkspaceService
from app.services.providers.errors import ProviderError


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261030T083000
SUMMARY:Employment Cost
 Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261106T083000
SUMMARY:Employment Situation
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261107
SUMMARY:All day entry
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261107T083000
SUMMARY:Cancelled release
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR"""
RSS = """<rss><channel><item><title>Policy announcement</title><link>https://www.ecb.europa.eu/news</link><pubDate>Wed, 28 Oct 2026 12:00:00 GMT</pubDate><description>&lt;b&gt;Official&lt;/b&gt; update</description></item><item><title>Unsafe link</title><link>javascript:alert(1)</link><pubDate>Wed, 28 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>"""


def test_bls_times_respect_dst_and_do_not_invent_values_or_all_day_times():
    rows = parse_calendar(ICS, date(2026, 10, 29), date(2026, 11, 10), 50)
    assert len(rows) == 2
    assert rows[0].event == "Employment CostIndex"
    assert rows[0].scheduled_at.hour == 12
    assert rows[1].scheduled_at.hour == 13
    assert rows[0].estimate is rows[0].actual is rows[0].impact is None
    assert rows[0].source == "BLS"
    assert len(parse_calendar(ICS, date(2026, 11, 1), date(2026, 11, 10), 50)) == 1


def test_news_strips_html_and_drops_unsafe_links_and_dtds():
    rows = parse_news(RSS, "ECB")
    assert len(rows) == 1
    assert rows[0].summary == "Official update"
    with pytest.raises(ValueError):
        parse_news('<!DOCTYPE rss [<!ENTITY test "bad">]><rss/>', "ECB")
    with pytest.raises(ValueError):
        parse_news('<html><body>Unavailable</body></html>', "ECB")
    with pytest.raises(ValueError):
        parse_calendar('<html>Unavailable</html>', date(2026,10,1), date(2026,11,1), 50)


@pytest.mark.asyncio
async def test_official_feeds_use_cache_and_preserve_partial_results():
    _cache.clear()
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(503) if "bls_latest" in str(request.url) else httpx.Response(200, text=RSS)
    client = PublicResearchClient(httpx.MockTransport(handler))
    try:
        rows, warnings = await client.news(10)
        assert len(rows) == 1 and rows[0].source == "ECB"
        assert warnings[0]["provider"] == "BLS"
        await client.news(10)
        assert sum("ecb.europa.eu" in url for url in calls) == 1
    finally:
        await client.aclose()
        _cache.clear()


@pytest.mark.asyncio
async def test_calendar_preserves_bea_when_bls_is_unreachable():
    _cache.clear()
    def handler(request):
        return httpx.Response(503) if "bls.gov" in str(request.url) else httpx.Response(200, text=ICS)
    client = PublicResearchClient(httpx.MockTransport(handler))
    try:
        rows = await client.calendar(date(2026,10,29), date(2026,11,10), 50)
        assert len(rows) == 2
        assert {row.source for row in rows} == {"BEA"}
        assert client.calendar_sources == ["BEA"]
        assert client.calendar_warnings[0]["provider"] == "BLS"
    finally:
        await client.aclose()
        _cache.clear()


@pytest.mark.asyncio
async def test_workspace_falls_back_without_confusing_official_dates_with_consensus_data():
    class Disabled:
        async def economic_calendar(self, *args):
            raise ProviderError("finnhub", "not_configured", "Disabled")
        async def market_news(self, *args):
            raise ProviderError("finnhub", "not_configured", "Disabled")
        async def aclose(self): pass
    class Official:
        async def calendar(self, start, end, limit): return parse_calendar(ICS, start, end, limit)
        async def news(self, limit): return parse_news(RSS, "ECB"), []
        async def aclose(self): pass
    service = MarketWorkspaceService(Disabled(), public=Official())
    # Non-forex avoids unrelated macro calls in this fallback test.
    try:
        calendar = await service.calendar(date(2026,10,29), date(2026,11,10), canonical_id="crypto:BTC/USD")
        assert calendar["sources"] == ["BLS"]
        assert calendar["items"][0]["source"] == "BLS"
        assert calendar["items"][0]["estimate"] is None
        news = await service.news()
        assert news["sources"] == ["ECB"]
        assert "Official" in news["coverage"]
    finally:
        await service.aclose()
