from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config.settings import settings


@dataclass(frozen=True)
class LimiterLease:
    connection_id: str
    owner: str
    waited_seconds: float


class TradeLockerRequestLimiter:
    """Cross-process SQLite lease and cooldown for candle-history requests."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tradelocker_request_limits(
                connection_id TEXT PRIMARY KEY, lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                next_allowed_at REAL NOT NULL DEFAULT 0, cooldown_until REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30, isolation_level=None)

    async def acquire(self, connection_id: str) -> LimiterLease:
        owner = uuid4().hex
        started = time.time()
        while True:
            now = time.time()
            wait_for = 0.0
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT lease_until,next_allowed_at,cooldown_until FROM tradelocker_request_limits WHERE connection_id=?",
                                 (connection_id,)).fetchone()
                if row is None:
                    db.execute("""INSERT INTO tradelocker_request_limits(
                        connection_id,lease_owner,lease_until,next_allowed_at,cooldown_until,updated_at)
                        VALUES(?,?,?,?,?,?)""", (connection_id, owner, now + 30,
                        now + settings.tradelocker_rate_limit_min_interval_seconds, 0,
                        datetime.now(timezone.utc).isoformat()))
                    db.execute("COMMIT")
                    return LimiterLease(connection_id, owner, time.time() - started)
                lease_until, next_allowed, cooldown = (float(value) for value in row)
                available_at = max(lease_until if lease_until > now else 0, next_allowed, cooldown)
                if available_at <= now:
                    db.execute("""UPDATE tradelocker_request_limits SET lease_owner=?,lease_until=?,
                        next_allowed_at=?,updated_at=? WHERE connection_id=?""",
                        (owner, now + 30, now + settings.tradelocker_rate_limit_min_interval_seconds,
                         datetime.now(timezone.utc).isoformat(), connection_id))
                    db.execute("COMMIT")
                    return LimiterLease(connection_id, owner, time.time() - started)
                wait_for = min(max(0.01, available_at - now), settings.tradelocker_rate_limit_max_backoff_seconds)
                db.execute("COMMIT")
            await asyncio.sleep(wait_for)

    def release(self, lease: LimiterLease) -> None:
        with self._connect() as db:
            db.execute("""UPDATE tradelocker_request_limits SET lease_owner=NULL,lease_until=0,
                updated_at=? WHERE connection_id=? AND lease_owner=?""",
                (datetime.now(timezone.utc).isoformat(), lease.connection_id, lease.owner))

    def set_cooldown(self, connection_id: str, seconds: float) -> float:
        until = time.time() + max(0, seconds)
        with self._connect() as db:
            db.execute("""INSERT INTO tradelocker_request_limits(
                connection_id,lease_owner,lease_until,next_allowed_at,cooldown_until,updated_at)
                VALUES(?,NULL,0,0,?,?) ON CONFLICT(connection_id) DO UPDATE SET
                cooldown_until=MAX(cooldown_until,excluded.cooldown_until),updated_at=excluded.updated_at""",
                (connection_id, until, datetime.now(timezone.utc).isoformat()))
        return until

    def state(self, connection_id: str) -> dict[str, float | str | None]:
        with self._connect() as db:
            row = db.execute("SELECT cooldown_until FROM tradelocker_request_limits WHERE connection_id=?",
                             (connection_id,)).fetchone()
        value = float(row[0]) if row else 0.0
        return {"cooldown_until": datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None,
                "cooldown_epoch": value}


tradelocker_request_limiter = TradeLockerRequestLimiter()
