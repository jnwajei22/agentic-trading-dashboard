from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from threading import RLock
from time import monotonic
from typing import Callable, Literal

from app.config.settings import settings
from app.models.providers import MarketSeries


CacheStatus = Literal["found", "not_found", "expired", "access_denied"]


@dataclass(frozen=True)
class CachedSeries:
    owner_id: str
    series_id: str
    series: MarketSeries
    expires_at: datetime
    expires_monotonic: float


class MarketSeriesCache:
    """Small user-scoped cache with a replaceable storage boundary."""

    def __init__(
        self,
        ttl_seconds: int = 600,
        max_items: int = 100,
        *,
        clock: Callable[[], float] = monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds < 1 or max_items < 1:
            raise ValueError("Cache TTL and maximum size must be positive.")
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._clock = clock
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._items: OrderedDict[tuple[str, str], CachedSeries] = OrderedDict()
        self._expired: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._lock = RLock()

    def put(self, owner_id: str, series: MarketSeries) -> CachedSeries:
        now = self._clock()
        expires_at = self._utcnow() + timedelta(seconds=self.ttl_seconds)
        with self._lock:
            self._evict_expired(now)
            while len(self._items) >= self.max_items:
                self._items.popitem(last=False)
            series_id = token_urlsafe(24)
            stored = series.model_copy(deep=True, update={
                "series_id": series_id,
                "series_expires_at": expires_at,
            })
            entry = CachedSeries(owner_id, series_id, stored, expires_at, now + self.ttl_seconds)
            self._items[(owner_id, series_id)] = entry
            return entry

    def get(self, owner_id: str, series_id: str) -> tuple[CacheStatus, MarketSeries | None]:
        now = self._clock()
        key = (owner_id, series_id)
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                if entry.expires_monotonic <= now:
                    del self._items[key]
                    self._remember_expired(key, now)
                    return "expired", None
                return "found", entry.series.model_copy(deep=True)
            self._evict_expired_tombstones(now)
            if key in self._expired:
                return "expired", None
            for other_key, other in list(self._items.items()):
                if other.expires_monotonic <= now:
                    del self._items[other_key]
                elif other.series_id == series_id:
                    return "access_denied", None
            return "not_found", None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._expired.clear()

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired(self._clock())
            return len(self._items)

    def _evict_expired(self, now: float) -> None:
        for key, entry in list(self._items.items()):
            if entry.expires_monotonic <= now:
                del self._items[key]
                self._remember_expired(key, now)
        self._evict_expired_tombstones(now)

    def _remember_expired(self, key: tuple[str, str], now: float) -> None:
        self._expired[key] = now + self.ttl_seconds
        while len(self._expired) > self.max_items:
            self._expired.popitem(last=False)

    def _evict_expired_tombstones(self, now: float) -> None:
        for key, forget_at in list(self._expired.items()):
            if forget_at <= now:
                del self._expired[key]


market_series_cache = MarketSeriesCache(
    ttl_seconds=settings.market_series_cache_ttl_seconds,
    max_items=settings.market_series_cache_max_items,
)
