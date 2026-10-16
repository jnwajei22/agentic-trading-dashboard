from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any, Callable

from app.config.settings import settings


@dataclass(frozen=True)
class TradeLockerConfigCacheKey:
    auth0_user_id: str
    environment: str
    server: str
    account_id: str
    account_number: str
    connection_id: str = ""
    account_record_id: str = ""


@dataclass(frozen=True)
class _Entry:
    value: dict[str, Any]
    expires_at: float


class TradeLockerConfigCache:
    def __init__(self, ttl_seconds: int = 900, *, clock: Callable[[], float] = monotonic) -> None:
        if ttl_seconds < 1:
            raise ValueError("TradeLocker config cache TTL must be positive.")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[TradeLockerConfigCacheKey, _Entry] = OrderedDict()
        self._lock = RLock()

    def get(self, key: TradeLockerConfigCacheKey) -> dict[str, Any] | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._items[key]
                return None
            return deepcopy(entry.value)

    def put(self, key: TradeLockerConfigCacheKey, value: dict[str, Any]) -> None:
        with self._lock:
            self._items[key] = _Entry(deepcopy(value), self._clock() + self.ttl_seconds)

    def invalidate_user(self, auth0_user_id: str) -> None:
        with self._lock:
            for key in list(self._items):
                if key.auth0_user_id == auth0_user_id:
                    del self._items[key]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


tradelocker_config_cache = TradeLockerConfigCache(
    ttl_seconds=settings.tradelocker_config_cache_ttl_seconds
)
