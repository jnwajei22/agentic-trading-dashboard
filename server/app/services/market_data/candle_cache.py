from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.config.settings import settings
from app.models.market import Candle
from app.services.market_data.history import (
    TIMEFRAME_DURATION_MS,
    normalize_timeframe,
    next_completed_candle_due_ms,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CandleCacheKey:
    user_id: str
    connection_id: str
    account_id: str
    account_number: str
    instrument_id: str
    symbol: str
    timeframe: str

    def normalized(self) -> "CandleCacheKey":
        return CandleCacheKey(
            self.user_id, self.connection_id, self.account_id, self.account_number,
            str(self.instrument_id), self.symbol.replace("/", "").upper(),
            normalize_timeframe(self.timeframe),
        )

    @property
    def diagnostic_id(self) -> str:
        current = self.normalized()
        return ":".join((current.connection_id, current.account_id, current.account_number,
                         current.instrument_id, current.symbol, current.timeframe))


@dataclass(frozen=True)
class CandleCacheEntry:
    key: CandleCacheKey
    candles: list[Candle]
    newest_completed_timestamp: int
    fetched_at: datetime
    expires_at: datetime
    source: str
    metadata: dict[str, Any]

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.fetched_at).total_seconds())

    def fresh(self, now: datetime) -> bool:
        return now <= self.expires_at

    def covers(
        self, *, required_count: int, start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> bool:
        selected = [
            candle for candle in self.candles
            if (start_time_ms is None or candle.timestamp >= start_time_ms)
            and (end_time_ms is None or candle.timestamp <= end_time_ms)
        ]
        if len(selected) < required_count:
            return False
        if start_time_ms is not None:
            duration = TIMEFRAME_DURATION_MS[normalize_timeframe(self.key.timeframe)]
            if selected[0].timestamp > start_time_ms + duration:
                return False
        return True


class DurableCandleCache:
    """SQLite cache shared by API, MCP, and scheduler processes."""

    def __init__(
        self, db_path: str | Path | None = None, *, max_entries: int | None = None,
        grace_seconds: int | None = None, clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.max_entries = max_entries or settings.tradelocker_candle_cache_max_entries
        self.grace_seconds = (settings.tradelocker_candle_cache_grace_seconds
                              if grace_seconds is None else grace_seconds)
        self.clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tradelocker_candle_cache(
                user_id TEXT NOT NULL, connection_id TEXT NOT NULL, account_id TEXT NOT NULL,
                account_number TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, candles_json TEXT NOT NULL,
                oldest_completed_timestamp INTEGER NOT NULL,
                newest_completed_timestamp INTEGER NOT NULL, fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, source TEXT NOT NULL, metadata_json TEXT NOT NULL,
                PRIMARY KEY(user_id,connection_id,account_id,account_number,instrument_id,symbol,timeframe)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_tradelocker_candle_cache_expiry
                ON tradelocker_candle_cache(expires_at,fetched_at)""")

    @staticmethod
    def _params(key: CandleCacheKey) -> tuple[str, ...]:
        value = key.normalized()
        return (value.user_id, value.connection_id, value.account_id, value.account_number,
                value.instrument_id, value.symbol, value.timeframe)

    def get(self, key: CandleCacheKey) -> CandleCacheEntry | None:
        normalized = key.normalized()
        with self._connect() as db:
            row = db.execute("""SELECT * FROM tradelocker_candle_cache WHERE
                user_id=? AND connection_id=? AND account_id=? AND account_number=?
                AND instrument_id=? AND symbol=? AND timeframe=?""", self._params(normalized)).fetchone()
        if row is None:
            return None
        try:
            candles = [Candle.model_validate(item) for item in json.loads(row["candles_json"])]
            metadata = json.loads(row["metadata_json"])
            return CandleCacheEntry(
                normalized, candles, int(row["newest_completed_timestamp"]),
                datetime.fromisoformat(row["fetched_at"]), datetime.fromisoformat(row["expires_at"]),
                str(row["source"]), metadata if isinstance(metadata, dict) else {},
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            self.delete(normalized)
            return None

    def put(
        self, key: CandleCacheKey, candles: list[Candle], *, source: str,
        metadata: dict[str, Any], fetched_at: datetime | None = None,
    ) -> CandleCacheEntry:
        if not candles:
            raise ValueError("Completed candles are required for cache storage.")
        normalized = key.normalized()
        ordered = sorted({item.timestamp: item for item in candles}.values(), key=lambda item: item.timestamp)
        now = fetched_at or self.clock()
        next_completion_ms = next_completed_candle_due_ms(
            ordered[-1].timestamp, normalized.timeframe
        )
        boundary_expiry = datetime.fromtimestamp(next_completion_ms / 1000, timezone.utc)
        expires_at = max(now, boundary_expiry) + timedelta(seconds=self.grace_seconds)
        values = (*self._params(normalized),
                  json.dumps([item.model_dump(mode="json") for item in ordered], separators=(",", ":")),
                  ordered[0].timestamp, ordered[-1].timestamp, now.isoformat(), expires_at.isoformat(),
                  source, json.dumps(metadata, separators=(",", ":"), sort_keys=True))
        with self._connect() as db:
            db.execute("""INSERT INTO tradelocker_candle_cache(
                user_id,connection_id,account_id,account_number,instrument_id,symbol,timeframe,
                candles_json,oldest_completed_timestamp,newest_completed_timestamp,fetched_at,
                expires_at,source,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,connection_id,account_id,account_number,instrument_id,symbol,timeframe)
                DO UPDATE SET candles_json=excluded.candles_json,
                oldest_completed_timestamp=excluded.oldest_completed_timestamp,
                newest_completed_timestamp=excluded.newest_completed_timestamp,
                fetched_at=excluded.fetched_at,expires_at=excluded.expires_at,
                source=excluded.source,metadata_json=excluded.metadata_json""", values)
            excess = db.execute("SELECT MAX(0,COUNT(*)-?) FROM tradelocker_candle_cache",
                                (self.max_entries,)).fetchone()[0]
            if excess:
                db.execute("""DELETE FROM tradelocker_candle_cache WHERE rowid IN
                    (SELECT rowid FROM tradelocker_candle_cache ORDER BY fetched_at LIMIT ?)""", (excess,))
            retention_cutoff = now - timedelta(
                seconds=settings.tradelocker_candle_cache_max_stale_seconds
            )
            db.execute(
                "DELETE FROM tradelocker_candle_cache WHERE fetched_at < ?",
                (retention_cutoff.isoformat(),),
            )
        return self.get(normalized)  # type: ignore[return-value]

    def delete(self, key: CandleCacheKey) -> None:
        with self._connect() as db:
            db.execute("""DELETE FROM tradelocker_candle_cache WHERE
                user_id=? AND connection_id=? AND account_id=? AND account_number=?
                AND instrument_id=? AND symbol=? AND timeframe=?""", self._params(key))

    def clear(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM tradelocker_candle_cache")


candle_cache = DurableCandleCache()
