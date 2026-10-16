import time
from typing import Any


class TTLCache:
    """Small in-process cache isolated behind a replaceable interface."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._values[key] = (time.monotonic() + ttl_seconds, value)

    def clear(self) -> None:
        self._values.clear()
