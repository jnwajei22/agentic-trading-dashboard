from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_NOTIFICATIONS = {
    "demo_trade_submitted": True, "trade_rejected": True, "strategy_blocked": True,
    "daily_loss_limit_reached": True, "trading_connection_disconnected": True,
    "schedule_failed": True, "automation_interruption": True, "daily_summary": False,
}
DEFAULT_FOREX_MAJORS = ["forex:EUR/USD", "forex:GBP/USD", "forex:USD/JPY",
                          "forex:USD/CHF", "forex:AUD/USD", "forex:USD/CAD", "forex:NZD/USD"]


class UserExperienceRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS user_preferences(
                    user_sub TEXT PRIMARY KEY,appearance TEXT NOT NULL DEFAULT 'system',
                    timezone TEXT NOT NULL DEFAULT 'America/Chicago',date_format TEXT NOT NULL DEFAULT 'locale',
                    time_format TEXT NOT NULL DEFAULT 'locale',currency_display TEXT NOT NULL DEFAULT 'account',
                    notification_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS user_watchlists(
                    id TEXT PRIMARY KEY,user_sub TEXT NOT NULL,name TEXT NOT NULL COLLATE NOCASE,
                    is_default INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    UNIQUE(user_sub,name));
                CREATE TABLE IF NOT EXISTS user_watchlist_items(
                    watchlist_id TEXT NOT NULL,user_sub TEXT NOT NULL,symbol TEXT NOT NULL,
                    position INTEGER NOT NULL,pinned INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
                    PRIMARY KEY(watchlist_id,symbol),
                    FOREIGN KEY(watchlist_id) REFERENCES user_watchlists(id) ON DELETE CASCADE);
            """)

    def preferences(self, user_sub: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM user_preferences WHERE user_sub=?", (user_sub,)).fetchone()
            if row is None:
                now = _now()
                db.execute("INSERT INTO user_preferences(user_sub,notification_json,updated_at) VALUES(?,?,?)",
                           (user_sub, json.dumps(DEFAULT_NOTIFICATIONS), now))
                row = db.execute("SELECT * FROM user_preferences WHERE user_sub=?", (user_sub,)).fetchone()
        result = dict(row)
        result["notifications"] = {**DEFAULT_NOTIFICATIONS, **json.loads(result.pop("notification_json"))}
        return result

    def update_preferences(self, user_sub: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.preferences(user_sub)
        allowed = {"appearance", "timezone", "date_format", "time_format", "currency_display"}
        updates = {key: value for key, value in changes.items() if key in allowed and value is not None}
        if "notifications" in changes and changes["notifications"] is not None:
            notifications = {**current["notifications"], **changes["notifications"]}
            updates["notification_json"] = json.dumps(notifications, separators=(",", ":"), sort_keys=True)
        updates["updated_at"] = _now()
        assignments = ",".join(f"{key}=:{key}" for key in updates)
        with self._connect() as db:
            db.execute(f"UPDATE user_preferences SET {assignments} WHERE user_sub=:user_sub",
                       {**updates, "user_sub": user_sub})
        return self.preferences(user_sub)

    def _ensure_default_watchlist(self, user_sub: str) -> None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM user_watchlists WHERE user_sub=?", (user_sub,)).fetchone():
                return
            now = _now(); watchlist_id = f"watchlist_{uuid4().hex}"
            db.execute("INSERT INTO user_watchlists VALUES(?,?,?,?,?,?)",
                       (watchlist_id, user_sub, "Forex Majors", 1, now, now))
            db.executemany("INSERT INTO user_watchlist_items VALUES(?,?,?,?,?,?)",
                           [(watchlist_id, user_sub, symbol, index, 0, now)
                            for index, symbol in enumerate(DEFAULT_FOREX_MAJORS)])

    def list_watchlists(self, user_sub: str) -> list[dict[str, Any]]:
        self._ensure_default_watchlist(user_sub)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM user_watchlists WHERE user_sub=? ORDER BY is_default DESC,created_at", (user_sub,)).fetchall()
            items = db.execute("SELECT * FROM user_watchlist_items WHERE user_sub=? ORDER BY position", (user_sub,)).fetchall()
        return [{**dict(row), "is_default": bool(row["is_default"]),
                 "items": [{**dict(item), "pinned": bool(item["pinned"])} for item in items if item["watchlist_id"] == row["id"]]}
                for row in rows]

    def create_watchlist(self, user_sub: str, name: str, symbols: list[str]) -> dict[str, Any]:
        watchlist_id = f"watchlist_{uuid4().hex}"; now = _now()
        clean = list(dict.fromkeys(symbol.strip() if ":" in symbol else symbol.strip().upper() for symbol in symbols if symbol.strip()))[:100]
        with self._connect() as db:
            db.execute("INSERT INTO user_watchlists VALUES(?,?,?,?,?,?)", (watchlist_id, user_sub, name.strip(), 0, now, now))
            db.executemany("INSERT INTO user_watchlist_items VALUES(?,?,?,?,?,?)",
                           [(watchlist_id, user_sub, symbol, index, 0, now) for index, symbol in enumerate(clean)])
        return next(item for item in self.list_watchlists(user_sub) if item["id"] == watchlist_id)

    def replace_watchlist(self, user_sub: str, watchlist_id: str, *, name: str | None,
                          items: list[dict[str, Any]]) -> dict[str, Any] | None:
        with self._connect() as db:
            owned = db.execute("SELECT 1 FROM user_watchlists WHERE id=? AND user_sub=?", (watchlist_id, user_sub)).fetchone()
            if not owned: return None
            if name: db.execute("UPDATE user_watchlists SET name=?,updated_at=? WHERE id=? AND user_sub=?",
                                (name.strip(), _now(), watchlist_id, user_sub))
            db.execute("DELETE FROM user_watchlist_items WHERE watchlist_id=? AND user_sub=?", (watchlist_id, user_sub))
            now = _now(); seen: set[str] = set(); normalized = []
            for item in items[:100]:
                raw = str(item.get("canonical_id") or item.get("symbol", "")).strip()
                symbol = raw if ":" in raw else raw.upper()
                if not symbol or symbol in seen: continue
                seen.add(symbol); normalized.append((watchlist_id, user_sub, symbol, len(normalized), bool(item.get("pinned")), now))
            db.executemany("INSERT INTO user_watchlist_items VALUES(?,?,?,?,?,?)", normalized)
            db.execute("UPDATE user_watchlists SET updated_at=? WHERE id=?", (now, watchlist_id))
        return next(item for item in self.list_watchlists(user_sub) if item["id"] == watchlist_id)

    def delete_watchlist(self, user_sub: str, watchlist_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT is_default FROM user_watchlists WHERE id=? AND user_sub=?", (watchlist_id, user_sub)).fetchone()
            if not row or row["is_default"]: return False
            return db.execute("DELETE FROM user_watchlists WHERE id=? AND user_sub=?", (watchlist_id, user_sub)).rowcount == 1
