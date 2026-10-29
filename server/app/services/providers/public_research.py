"""Keyless official research feeds; never used for execution or risk clearance."""
from __future__ import annotations

import asyncio
import html
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from app.models.providers import EconomicEvent, MarketNewsItem
from app.services.providers.cache import TTLCache
from app.services.providers.errors import ProviderError

_cache = TTLCache()
FEEDS = (("BLS", "https://www.bls.gov/feed/bls_latest.rss"),
         ("ECB", "https://www.ecb.europa.eu/rss/press.html"))
CALENDAR = "https://www.bls.gov/schedule/news_release/bls.ics"


def plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def parse_news(payload: str, source: str) -> list[MarketNewsItem]:
    if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
        raise ValueError("DTD content is not supported")
    root = ElementTree.fromstring(payload)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Invalid RSS feed")
    result = []
    for item in root.findall("./channel/item")[:50]:
        title, link = plain(item.findtext("title", "")), item.findtext("link", "").strip()
        if not title or urlparse(link).scheme != "https":
            continue
        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate", ""))
            if stamp.tzinfo is None:
                continue
        except (ValueError, TypeError):
            continue
        result.append(MarketNewsItem(headline=title[:300], summary=plain(item.findtext("description", ""))[:400] or None,
            published_at=stamp.astimezone(timezone.utc), source_name=source, source=source, url=link))
    return result


def parse_calendar(payload: str, start: date, end: date, limit: int) -> list[EconomicEvent]:
    if "BEGIN:VCALENDAR" not in payload or "END:VCALENDAR" not in payload:
        raise ValueError("Invalid calendar feed")
    # RFC 5545 folded lines; BLS supplies Eastern time including DST transitions.
    unfolded = re.sub(r"\r?\n[ \t]", "", payload)
    events = []
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT", 1)[0]
        fields = {}
        zone = "America/New_York"
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            name = key.split(";", 1)[0]
            fields[name] = value
            if name == "DTSTART" and "TZID=" in key:
                zone = key.split("TZID=", 1)[1].strip('"')
        if fields.get("STATUS") == "CANCELLED" or not fields.get("SUMMARY"):
            continue
        raw = fields.get("DTSTART", "")
        if "T" not in raw:  # Do not fabricate a release time for all-day entries.
            continue
        try:
            tz = timezone.utc if raw.endswith("Z") else ZoneInfo("America/New_York" if zone == "US-Eastern" else zone)
            stamp = datetime.strptime(raw.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        except (ValueError, KeyError):
            continue
        if not start <= stamp.astimezone(ZoneInfo("America/New_York")).date() <= end:
            continue
        title = fields["SUMMARY"].replace(r"\,", ",").replace(r"\n", " ").replace(r"\;", ";")
        events.append(EconomicEvent(event=title[:300], country="US", currency="USD", scheduled_at=stamp.astimezone(timezone.utc), source="BLS"))
    unique = {(event.event, event.scheduled_at): event for event in events}
    return sorted(unique.values(), key=lambda event: event.scheduled_at)[:limit]


class PublicResearchClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = httpx.AsyncClient(timeout=10, transport=transport)

    async def _get(self, url: str) -> str:
        cached = _cache.get(url)
        if cached is not None:
            return cached
        try:
            async with self.http.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1_000_000:
                        raise ValueError("Feed is too large")
            text = content.decode("utf-8-sig")
            if not text.strip():
                raise ValueError("Empty feed")
            _cache.set(url, text, 300)
            return text
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ProviderError("official_releases", "upstream_failure", "An official research feed is temporarily unavailable.", retryable=True) from None

    async def news(self, limit: int) -> tuple[list[MarketNewsItem], list[dict]]:
        rows = await asyncio.gather(*(self._get(url) for _, url in FEEDS), return_exceptions=True)
        items, warnings = [], []
        for (source, _), row in zip(FEEDS, rows):
            try:
                if isinstance(row, BaseException):
                    raise row
                items.extend(parse_news(row, source))
            except Exception:
                warnings.append({"provider": source, "error": "upstream_failure", "message": f"{source} announcements are temporarily unavailable."})
        unique = {(item.url, item.headline): item for item in items}
        return sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit], warnings

    async def calendar(self, start: date, end: date, limit: int) -> list[EconomicEvent]:
        return parse_calendar(await self._get(CALENDAR), start, end, limit)

    async def aclose(self) -> None:
        await self.http.aclose()
