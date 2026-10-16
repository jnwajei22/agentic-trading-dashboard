from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config.settings import settings


DEFAULT_RUNTIME_SETTINGS = {
    "global_kill_switch": True, "demo_execution_enabled": False,
    "live_execution_enabled": False, "maintenance_mode": False,
    "enabled_providers": ["tradelocker", "tradingview_chart"],
    "default_ai_model": "gpt-5.6", "default_decision_provider": "no_trade",
    "concurrency_limit": 4, "per_user_usage_limit": 1000,
    "feature_flags": {}, "registration_policy": "authenticated",
}


class RuntimeSettingsRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS platform_runtime_settings(
                key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def get_all(self) -> dict[str, Any]:
        result = dict(DEFAULT_RUNTIME_SETTINGS)
        with sqlite3.connect(self.db_path) as db:
            for key, value in db.execute("SELECT key,value_json FROM platform_runtime_settings"):
                result[key] = json.loads(value)
        return result

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = DEFAULT_RUNTIME_SETTINGS.keys()
        with sqlite3.connect(self.db_path) as db:
            for key, value in values.items():
                if key not in allowed: raise ValueError(f"Unsupported runtime setting: {key}")
                db.execute("""INSERT INTO platform_runtime_settings(key,value_json) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP""",
                    (key, json.dumps(value)))
        return self.get_all()


class AdminConfigurationService:
    def __init__(self, repository: RuntimeSettingsRepository | None = None) -> None:
        self.repository = repository or RuntimeSettingsRepository()

    def current(self) -> dict[str, Any]: return self.repository.get_all()
    def apply(self, values: dict[str, Any]) -> dict[str, Any]: return self.repository.update(values)
