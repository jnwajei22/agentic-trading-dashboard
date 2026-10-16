from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.storage.database import connect, storage_path, using_postgres, IntegrityError


class SignalIntentRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = storage_path(db_path)

        if using_postgres(): return
        with connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS signal_intents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,provider_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,canonical_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending_validation',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def create(self, key: str, canonical_id: str, payload: dict[str, Any]) -> bool:
        with connect(self.db_path) as db:
            try:
                db.execute("INSERT INTO signal_intents(provider_type,idempotency_key,canonical_id,payload_json) VALUES('tradingview_signal',?,?,?)",
                    (key, canonical_id, json.dumps(payload, default=str)))
                return True
            except IntegrityError:
                return False
