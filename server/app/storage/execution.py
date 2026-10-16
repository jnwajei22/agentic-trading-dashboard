from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.models.autonomous import ExecutionMode


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionRepository:
    """Durable, account-scoped autonomous-demo state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_sub TEXT NOT NULL, connection_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, acc_num TEXT NOT NULL,
                    execution_mode TEXT NOT NULL DEFAULT 'read_only'
                      CHECK(execution_mode IN ('read_only','demo_manual','demo_autonomous')),
                    strategy_name TEXT NOT NULL DEFAULT 'ai_competition_v1',
                    strategy_version TEXT NOT NULL DEFAULT '1.0',
                    risk_per_trade_percent REAL NOT NULL DEFAULT 0.25,
                    daily_loss_limit_percent REAL NOT NULL DEFAULT 3.0,
                    drawdown_cutoff_percent REAL NOT NULL DEFAULT 10.0,
                    maximum_open_positions INTEGER NOT NULL DEFAULT 1,
                    maximum_pending_orders INTEGER NOT NULL DEFAULT 1,
                    maximum_new_entries_per_day INTEGER NOT NULL DEFAULT 2,
                    minimum_reward_risk REAL NOT NULL DEFAULT 1.5,
                    equity_high_watermark REAL,
                    allowed_pairs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_sub, connection_id, account_id, acc_num)
                );
                CREATE TABLE IF NOT EXISTS operational_controls(
                    key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT
                );
                CREATE TABLE IF NOT EXISTS autonomous_controls(
                    user_sub TEXT PRIMARY KEY,
                    global_autonomous_kill_switch INTEGER NOT NULL DEFAULT 1,
                    demo_autonomous_enabled INTEGER NOT NULL DEFAULT 0,
                    live_autonomous_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,updated_by TEXT NOT NULL,
                    last_change_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS autonomous_control_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,user_sub TEXT NOT NULL,
                    control_name TEXT NOT NULL,old_value INTEGER NOT NULL,new_value INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,changed_by TEXT NOT NULL,source TEXT NOT NULL,
                    reason TEXT
                );
                CREATE INDEX IF NOT EXISTS autonomous_control_audit_owner
                    ON autonomous_control_audit(user_sub,changed_at DESC);
                CREATE TABLE IF NOT EXISTS autonomous_snapshots (
                    id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, connection_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, acc_num TEXT NOT NULL, environment TEXT NOT NULL,
                    strategy_name TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    normalized_snapshot_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS autonomous_order_previews (
                    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, user_sub TEXT NOT NULL,
                    connection_id TEXT NOT NULL, account_id TEXT NOT NULL, acc_num TEXT NOT NULL,
                    profile_ref TEXT, account_record_id TEXT, connection_ref TEXT, account_alias TEXT,
                    server TEXT, base_url TEXT, demo_classification TEXT,
                    environment TEXT NOT NULL, pair TEXT NOT NULL, instrument_id TEXT NOT NULL,
                    route_id TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
                    entry REAL NOT NULL, stop_loss REAL NOT NULL, take_profit REAL NOT NULL,
                    quantity REAL NOT NULL, lot_size REAL NOT NULL, estimated_risk REAL NOT NULL,
                    risk_percent REAL NOT NULL, estimated_reward REAL NOT NULL, reward_risk REAL NOT NULL,
                    broker_metadata_json TEXT NOT NULL, status TEXT NOT NULL,
                    violations_json TEXT NOT NULL, expires_at TEXT NOT NULL,
                    submitted_at TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES autonomous_snapshots(id)
                );
                CREATE TABLE IF NOT EXISTS autonomous_runs (
                    id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, connection_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, acc_num TEXT NOT NULL, snapshot_id TEXT,
                    preview_id TEXT, strategy_name TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    decision TEXT NOT NULL, selected_pair TEXT, selected_side TEXT,
                    no_trade_reason_codes_json TEXT NOT NULL DEFAULT '[]',
                    rejection_reason_codes_json TEXT NOT NULL DEFAULT '[]',
                    result_status TEXT NOT NULL, broker_order_id TEXT,
                    result_json TEXT NOT NULL, started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS autonomous_decision_runs (
                    id TEXT PRIMARY KEY, run_key TEXT NOT NULL, user_sub TEXT NOT NULL,
                    profile_ref TEXT NOT NULL, account_record_id TEXT, connection_ref TEXT,
                    strategy_ref TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    decision_provider TEXT NOT NULL, model_identifier TEXT,
                    trigger_reason TEXT NOT NULL, state TEXT NOT NULL, shadow_mode INTEGER NOT NULL DEFAULT 1,
                    snapshot_id TEXT, context_hash TEXT, context_json TEXT NOT NULL DEFAULT '{}',
                    decision_json TEXT NOT NULL DEFAULT '{}', validation_json TEXT NOT NULL DEFAULT '{}',
                    execution_json TEXT NOT NULL DEFAULT '{}',
                    preview_id TEXT, execution_id TEXT, reason_codes_json TEXT NOT NULL DEFAULT '[]',
                    usage_json TEXT NOT NULL DEFAULT '{}', provider_latency_ms INTEGER,
                    started_at TEXT NOT NULL, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_sub,profile_ref,run_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_autonomous_decision_run
                    ON autonomous_decision_runs(user_sub,profile_ref)
                    WHERE state IN ('claimed','snapshotting','deciding','validating','previewing','submitting');
                CREATE TABLE IF NOT EXISTS broker_submissions (
                    id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE, request_fingerprint TEXT NOT NULL,
                    submission_state TEXT NOT NULL, broker_order_id TEXT UNIQUE,
                    broker_position_id TEXT, broker_response_sanitized_json TEXT NOT NULL DEFAULT '{}',
                    reconciliation_json TEXT NOT NULL DEFAULT '{}', submitted_at TEXT,
                    verified_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(preview_id) REFERENCES autonomous_order_previews(id)
                );
                CREATE TABLE IF NOT EXISTS demo_action_previews (
                    id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, action_type TEXT NOT NULL,
                    profile_ref TEXT NOT NULL, account_record_id TEXT NOT NULL, connection_ref TEXT NOT NULL,
                    connection_id TEXT NOT NULL, account_id TEXT NOT NULL, acc_num TEXT NOT NULL,
                    account_alias TEXT NOT NULL, environment TEXT NOT NULL, server TEXT NOT NULL, base_url TEXT NOT NULL,
                    demo_classification TEXT NOT NULL, target_id TEXT NOT NULL, target_json TEXT NOT NULL,
                    status TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_action_executions (
                    id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE, user_sub TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, action_type TEXT NOT NULL, target_id TEXT NOT NULL,
                    state TEXT NOT NULL, broker_response_json TEXT NOT NULL DEFAULT '{}',
                    reconciliation_json TEXT NOT NULL DEFAULT '{}', error_category TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT,
                    UNIQUE(user_sub,idempotency_key),
                    FOREIGN KEY(preview_id) REFERENCES demo_action_previews(id)
                );
                CREATE TABLE IF NOT EXISTS demo_reconciliation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL,
                    state TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES demo_action_executions(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(execution_settings)")}
            if "equity_high_watermark" not in columns:
                db.execute("ALTER TABLE execution_settings ADD COLUMN equity_high_watermark REAL")
            if "maximum_new_entries_per_day" not in columns:
                db.execute("ALTER TABLE execution_settings ADD COLUMN maximum_new_entries_per_day INTEGER NOT NULL DEFAULT 2")
            preview_columns = {row["name"] for row in db.execute("PRAGMA table_info(autonomous_order_previews)")}
            for name in ("profile_ref","account_record_id","connection_ref","account_alias","server","base_url","demo_classification"):
                if name not in preview_columns:
                    db.execute(f"ALTER TABLE autonomous_order_previews ADD COLUMN {name} TEXT")
            submission_columns={row["name"] for row in db.execute("PRAGMA table_info(broker_submissions)")}
            if "execution_id" not in submission_columns:
                db.execute("ALTER TABLE broker_submissions ADD COLUMN execution_id TEXT")
            if "execution_origin" not in preview_columns:
                db.execute("ALTER TABLE autonomous_order_previews ADD COLUMN execution_origin TEXT NOT NULL DEFAULT 'manual'")
            if "estimated_margin" not in preview_columns:
                db.execute("ALTER TABLE autonomous_order_previews ADD COLUMN estimated_margin REAL")
            decision_columns={row["name"] for row in db.execute("PRAGMA table_info(autonomous_decision_runs)")}
            if "execution_json" not in decision_columns:
                db.execute("ALTER TABLE autonomous_decision_runs ADD COLUMN execution_json TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _safe_audit_text(value: str | None, limit: int) -> str | None:
        if value is None:
            return None
        return " ".join(str(value).split())[:limit] or None

    def _ensure_autonomous_controls(self, db: sqlite3.Connection, user_sub: str) -> None:
        if db.execute("SELECT 1 FROM autonomous_controls WHERE user_sub=?", (user_sub,)).fetchone():
            return
        legacy = db.execute("SELECT value FROM operational_controls WHERE key='kill_switch'").fetchone()
        kill_switch = settings.kill_switch_enabled if legacy is None else legacy["value"] == "enabled"
        demo_enabled = False
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"users", "execution_profiles"}.issubset(tables):
            columns = {row["name"] for row in db.execute("PRAGMA table_info(execution_profiles)")}
            if "autonomous_armed" in columns:
                demo_enabled = db.execute(
                    """SELECT 1 FROM execution_profiles p JOIN users u ON u.id=p.user_id
                       WHERE u.auth0_sub=? AND p.enabled=1 AND p.autonomous_armed=1
                       AND p.execution_mode='demo_autonomous' LIMIT 1""", (user_sub,),
                ).fetchone() is not None
        now = utcnow().isoformat()
        db.execute(
            """INSERT INTO autonomous_controls(user_sub,global_autonomous_kill_switch,
               demo_autonomous_enabled,live_autonomous_enabled,updated_at,updated_by,last_change_reason)
               VALUES(?,?,?,?,?,?,?)""",
            (user_sub, kill_switch, demo_enabled, False, now, "migration", "Legacy-safe control migration"),
        )

    def get_autonomous_controls(self, user_sub: str) -> dict[str, Any]:
        with self._connect() as db:
            self._ensure_autonomous_controls(db, user_sub)
            row = db.execute("SELECT * FROM autonomous_controls WHERE user_sub=?", (user_sub,)).fetchone()
        result = dict(row) if row else {}
        for key in ("global_autonomous_kill_switch", "demo_autonomous_enabled", "live_autonomous_enabled"):
            result[key] = bool(result.get(key))
        result["live_execution_supported"] = False
        result["effective"] = {
            "demo": "blocked" if result["global_autonomous_kill_switch"] else "active" if result["demo_autonomous_enabled"] else "manual",
            "live": "blocked" if result["global_autonomous_kill_switch"] else "unsupported" if result["live_autonomous_enabled"] else "manual",
        }
        return result

    def update_autonomous_controls(
        self, user_sub: str, changes: dict[str, bool], *, updated_by: str,
        source: str = "dashboard", reason: str | None = None,
        audit_unchanged: bool = False,
    ) -> dict[str, Any]:
        allowed = {"global_autonomous_kill_switch", "demo_autonomous_enabled", "live_autonomous_enabled"}
        if not changes or set(changes) - allowed or any(not isinstance(value, bool) for value in changes.values()):
            raise ValueError("Autonomous control update is invalid.")
        now = utcnow().isoformat(); safe_reason = self._safe_audit_text(reason, 240)
        safe_source = self._safe_audit_text(source, 40) or "unknown"
        safe_actor = self._safe_audit_text(updated_by, 160) or user_sub
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_autonomous_controls(db, user_sub)
            current = db.execute("SELECT * FROM autonomous_controls WHERE user_sub=?", (user_sub,)).fetchone()
            for key, value in changes.items():
                old = bool(current[key])
                if old == value:
                    if audit_unchanged:
                        db.execute("""INSERT INTO autonomous_control_audit(user_sub,control_name,old_value,new_value,
                            changed_at,changed_by,source,reason) VALUES(?,?,?,?,?,?,?,?)""",
                            (user_sub, key, old, value, now, safe_actor, safe_source, safe_reason))
                    continue
                db.execute(f"UPDATE autonomous_controls SET {key}=?,updated_at=?,updated_by=?,last_change_reason=? WHERE user_sub=?",
                    (value, now, safe_actor, safe_reason, user_sub))
                db.execute("""INSERT INTO autonomous_control_audit(user_sub,control_name,old_value,new_value,
                    changed_at,changed_by,source,reason) VALUES(?,?,?,?,?,?,?,?)""",
                    (user_sub, key, old, value, now, safe_actor, safe_source, safe_reason))
        return self.get_autonomous_controls(user_sub)

    def autonomous_control_audit(self, user_sub: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM autonomous_control_audit WHERE user_sub=? ORDER BY id DESC LIMIT ?",
                              (user_sub, max(1, min(limit, 200)))).fetchall()
        return [{**dict(row), "old_value": bool(row["old_value"]), "new_value": bool(row["new_value"])} for row in rows]

    def kill_switch_enabled(self, user_sub: str | None = None)->bool:
        if user_sub is not None:
            return self.get_autonomous_controls(user_sub)["global_autonomous_kill_switch"]
        with self._connect() as db:row=db.execute("SELECT value FROM operational_controls WHERE key='kill_switch'").fetchone()
        return settings.kill_switch_enabled if row is None else row["value"]=="enabled"

    def enable_kill_switch(self,updated_by:str,*,source:str="compatibility",reason:str|None=None)->None:
        now=utcnow().isoformat()
        with self._connect() as db:db.execute("""INSERT INTO operational_controls(key,value,updated_at,updated_by)
            VALUES('kill_switch','enabled',?,?) ON CONFLICT(key) DO UPDATE SET value='enabled',updated_at=excluded.updated_at,
            updated_by=excluded.updated_by""",(now,updated_by))
        self.update_autonomous_controls(updated_by, {"global_autonomous_kill_switch": True},
            updated_by=updated_by, source=source, reason=reason)

    def get_or_create_settings(self, user_sub: str, connection_id: str, account_id: str, acc_num: str) -> dict[str, Any]:
        now = utcnow().isoformat()
        pairs = json.dumps(["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD"])
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO execution_settings(
                    user_sub, connection_id, account_id, acc_num, allowed_pairs_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_sub, connection_id, account_id, acc_num, pairs, now, now),
            )
            row = db.execute(
                """SELECT * FROM execution_settings WHERE user_sub=? AND connection_id=?
                   AND account_id=? AND acc_num=?""", (user_sub, connection_id, account_id, acc_num)
            ).fetchone()
        return self._decode(row)

    def set_mode(self, user_sub: str, connection_id: str, account_id: str, acc_num: str, mode: ExecutionMode) -> dict[str, Any]:
        self.get_or_create_settings(user_sub, connection_id, account_id, acc_num)
        with self._connect() as db:
            db.execute(
                """UPDATE execution_settings SET execution_mode=?, updated_at=? WHERE user_sub=?
                   AND connection_id=? AND account_id=? AND acc_num=?""",
                (mode.value, utcnow().isoformat(), user_sub, connection_id, account_id, acc_num),
            )
        return self.get_or_create_settings(user_sub, connection_id, account_id, acc_num)

    def observe_equity(self, user_sub: str, connection_id: str, account_id: str, acc_num: str, equity: float) -> float:
        if not math.isfinite(equity) or equity < 0:
            raise ValueError("equity must be a non-negative finite number")
        with self._connect() as db:
            db.execute(
                """UPDATE execution_settings SET equity_high_watermark = CASE
                     WHEN equity_high_watermark IS NULL OR equity_high_watermark < ? THEN ?
                     ELSE equity_high_watermark END, updated_at=?
                   WHERE user_sub=? AND connection_id=? AND account_id=? AND acc_num=?""",
                (equity, equity, utcnow().isoformat(), user_sub, connection_id, account_id, acc_num),
            )
            row = db.execute(
                """SELECT equity_high_watermark FROM execution_settings WHERE user_sub=?
                   AND connection_id=? AND account_id=? AND acc_num=?""",
                (user_sub, connection_id, account_id, acc_num),
            ).fetchone()
        return float(row["equity_high_watermark"])

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise KeyError("record not found")
        result = dict(row)
        for key in ("allowed_pairs_json", "normalized_snapshot_json", "violations_json", "broker_metadata_json", "result_json", "reconciliation_json", "broker_response_sanitized_json", "no_trade_reason_codes_json", "rejection_reason_codes_json", "context_json", "decision_json", "validation_json", "execution_json", "reason_codes_json", "usage_json"):
            if key in result:
                result[key.removesuffix("_json")] = json.loads(result.pop(key))
        return result

    def insert_snapshot(self, record: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO autonomous_snapshots VALUES
                   (:id,:user_sub,:connection_id,:account_id,:acc_num,:environment,
                    :strategy_name,:strategy_version,:normalized_snapshot_json,
                    :retrieved_at,:expires_at,:created_at)""", record,
            )

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM autonomous_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return self._decode(row) if row else None

    def insert_preview(self, record: dict[str, Any]) -> None:
        columns = ",".join(record)
        values = ",".join(f":{key}" for key in record)
        with self._connect() as db:
            db.execute(f"INSERT INTO autonomous_order_previews({columns}) VALUES ({values})", record)

    def get_preview(self, preview_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM autonomous_order_previews WHERE id=?", (preview_id,)).fetchone()
        return self._decode(row) if row else None

    def has_active_preview(self, user_sub: str, account_id: str, acc_num: str, pair: str, now: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """SELECT 1 FROM autonomous_order_previews WHERE user_sub=? AND account_id=?
                   AND acc_num=? AND pair=? AND status='approved' AND expires_at>? LIMIT 1""",
                (user_sub, account_id, acc_num, pair, now),
            ).fetchone()
        return row is not None

    def profile_broker_ids(self,user_sub:str,profile_ref:str)->dict[str,Any]:
        with self._connect() as db:
            rows=db.execute("""SELECT s.broker_order_id,s.broker_position_id,p.estimated_risk,p.estimated_margin FROM broker_submissions s
                JOIN autonomous_order_previews p ON p.id=s.preview_id
                WHERE p.user_sub=? AND p.profile_ref=? AND s.submission_state IN ('submitted','verified','accepted')""",
                (user_sub,profile_ref)).fetchall()
        return {"order_ids":{str(row["broker_order_id"]) for row in rows if row["broker_order_id"]},
            "position_ids":{str(row["broker_position_id"]) for row in rows if row["broker_position_id"]},
            "estimated_open_risk":sum(float(row["estimated_risk"] or 0) for row in rows if row["broker_position_id"]),
            "estimated_margin_used":sum(float(row["estimated_margin"] or 0) for row in rows if row["broker_position_id"])}

    def claim_submission(self, submission_id: str, preview_id: str, idempotency_key: str, fingerprint: str) -> tuple[bool, dict[str, Any]]:
        now = utcnow().isoformat()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO broker_submissions(id,preview_id,idempotency_key,request_fingerprint,
                       submission_state,created_at,updated_at) VALUES (?,?,?,?, 'claimed',?,?)""",
                    (submission_id, preview_id, idempotency_key, fingerprint, now, now),
                )
            return True, self.get_submission(preview_id=preview_id) or {}
        except sqlite3.IntegrityError:
            existing = self.get_submission(preview_id=preview_id) or self.get_submission(idempotency_key=idempotency_key)
            return False, existing or {}

    def get_submission(self, *, preview_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any] | None:
        key, value = ("preview_id", preview_id) if preview_id is not None else ("idempotency_key", idempotency_key)
        with self._connect() as db:
            row = db.execute(f"SELECT * FROM broker_submissions WHERE {key}=?", (value,)).fetchone()
        return self._decode(row) if row else None

    def get_submission_by_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM broker_submissions WHERE execution_id=?", (execution_id,)).fetchone()
        return self._decode(row) if row else None

    def update_submission(self, submission_id: str, **updates: Any) -> None:
        updates["updated_at"] = utcnow().isoformat()
        encoded = {key: json.dumps(value, separators=(",", ":"), sort_keys=True) if key.endswith("_json") else value for key, value in updates.items()}
        assignments = ",".join(f"{key}=:{key}" for key in encoded)
        with self._connect() as db:
            db.execute(f"UPDATE broker_submissions SET {assignments} WHERE id=:id", {**encoded, "id": submission_id})

    def mark_preview_submitted(self, preview_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE autonomous_order_previews SET status='submitted',submitted_at=? WHERE id=?", (utcnow().isoformat(), preview_id))

    def insert_run(self, record: dict[str, Any]) -> None:
        encoded = {key: json.dumps(value, separators=(",", ":"), sort_keys=True) if key.endswith("_json") else value for key, value in record.items()}
        columns = ",".join(encoded)
        values = ",".join(f":{key}" for key in encoded)
        with self._connect() as db:
            db.execute(f"INSERT INTO autonomous_runs({columns}) VALUES ({values})", encoded)

    def claim_decision_run(self, record: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        encoded={key:json.dumps(value,separators=(",",":"),sort_keys=True) if key.endswith("_json") else value for key,value in record.items()}
        columns=",".join(encoded); values=",".join(f":{key}" for key in encoded)
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(f"INSERT INTO autonomous_decision_runs({columns}) VALUES ({values})",encoded)
            return True,self.get_decision_run(record["user_sub"],record["id"]) or {}
        except sqlite3.IntegrityError:
            with self._connect() as db:
                row=db.execute("""SELECT * FROM autonomous_decision_runs WHERE user_sub=? AND profile_ref=?
                    AND (run_key=? OR state IN ('claimed','snapshotting','deciding','validating','previewing','submitting'))
                    ORDER BY (run_key=?) DESC,created_at DESC LIMIT 1""",
                    (record["user_sub"],record["profile_ref"],record["run_key"],record["run_key"])).fetchone()
            return False,self._decode(row) if row else {}

    def update_decision_run(self, run_id: str, **updates: Any) -> None:
        updates["updated_at"]=utcnow().isoformat()
        encoded={key:json.dumps(value,separators=(",",":"),sort_keys=True) if key.endswith("_json") else value for key,value in updates.items()}
        assignments=",".join(f"{key}=:{key}" for key in encoded)
        with self._connect() as db:
            db.execute(f"UPDATE autonomous_decision_runs SET {assignments} WHERE id=:id",{**encoded,"id":run_id})

    def get_decision_run(self,user_sub:str,run_id:str|None=None,*,profile_ref:str|None=None)->dict[str,Any]|None:
        with self._connect() as db:
            if run_id:
                row=db.execute("SELECT * FROM autonomous_decision_runs WHERE id=? AND user_sub=?",(run_id,user_sub)).fetchone()
            elif profile_ref:
                row=db.execute("SELECT * FROM autonomous_decision_runs WHERE user_sub=? AND profile_ref=? ORDER BY created_at DESC LIMIT 1",(user_sub,profile_ref)).fetchone()
            else:
                row=db.execute("SELECT * FROM autonomous_decision_runs WHERE user_sub=? ORDER BY created_at DESC LIMIT 1",(user_sub,)).fetchone()
        return self._decode(row) if row else None

    def get_decision_run_by_id(self,run_id:str)->dict[str,Any]|None:
        with self._connect() as db:row=db.execute("SELECT * FROM autonomous_decision_runs WHERE id=?",(run_id,)).fetchone()
        return self._decode(row) if row else None

    def recent_decision_runs(self,user_sub:str,limit:int=20)->list[dict[str,Any]]:
        with self._connect() as db:
            rows=db.execute("SELECT * FROM autonomous_decision_runs WHERE user_sub=? ORDER BY created_at DESC LIMIT ?",(user_sub,limit)).fetchall()
        return [self._decode(row) for row in rows]

    def get_run(self, user_sub: str, run_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            if run_id:
                row = db.execute("SELECT * FROM autonomous_runs WHERE id=? AND user_sub=?", (run_id, user_sub)).fetchone()
            else:
                row = db.execute("SELECT * FROM autonomous_runs WHERE user_sub=? ORDER BY created_at DESC LIMIT 1", (user_sub,)).fetchone()
        return self._decode(row) if row else None

    def update_run(self, run_id: str, **updates: Any) -> None:
        encoded = {
            key: json.dumps(value, separators=(",", ":"), sort_keys=True)
            if key.endswith("_json") else value
            for key, value in updates.items()
        }
        assignments = ",".join(f"{key}=:{key}" for key in encoded)
        with self._connect() as db:
            db.execute(f"UPDATE autonomous_runs SET {assignments} WHERE id=:id", {**encoded, "id": run_id})

    def insert_action_preview(self, record: dict[str,Any]) -> None:
        encoded={**record,"target_json":json.dumps(record["target_json"],separators=(",",":"),sort_keys=True)}
        with self._connect() as db:
            db.execute("""INSERT INTO demo_action_previews(id,user_sub,action_type,profile_ref,account_record_id,connection_ref,
                connection_id,account_id,acc_num,account_alias,environment,server,base_url,demo_classification,target_id,
                target_json,status,expires_at,created_at) VALUES(:id,:user_sub,:action_type,:profile_ref,:account_record_id,:connection_ref,
                :connection_id,:account_id,:acc_num,:account_alias,:environment,:server,:base_url,:demo_classification,:target_id,
                :target_json,:status,:expires_at,:created_at)""",encoded)

    def get_action_preview(self, preview_id:str) -> dict[str,Any]|None:
        with self._connect() as db: row=db.execute("SELECT * FROM demo_action_previews WHERE id=?",(preview_id,)).fetchone()
        if not row:return None
        result=dict(row);result["target"]=json.loads(result.pop("target_json"));return result

    def claim_action(self, execution_id:str, preview_id:str, user_sub:str, idempotency_key:str, action_type:str, target_id:str) -> tuple[bool,dict[str,Any]]:
        now=utcnow().isoformat()
        try:
            with self._connect() as db:
                db.execute("""INSERT INTO demo_action_executions(id,preview_id,user_sub,idempotency_key,action_type,target_id,state,created_at)
                    VALUES(?,?,?,?,?,?,'claimed',?)""",(execution_id,preview_id,user_sub,idempotency_key,action_type,target_id,now))
            return True,self.get_action_execution(user_sub,execution_id) or {}
        except sqlite3.IntegrityError:
            with self._connect() as db:
                row=db.execute("SELECT * FROM demo_action_executions WHERE preview_id=? OR (user_sub=? AND idempotency_key=?)",(preview_id,user_sub,idempotency_key)).fetchone()
            return False,self._decode(row) if row else {}

    def update_action_execution(self, execution_id:str, *, state:str, broker_response:dict|None=None,
                                reconciliation:dict|None=None,error_category:str|None=None) -> None:
        with self._connect() as db:
            db.execute("""UPDATE demo_action_executions SET state=?,broker_response_json=?,reconciliation_json=?,error_category=?,completed_at=? WHERE id=?""",
                (state,json.dumps(broker_response or {}),json.dumps(reconciliation or {}),error_category,utcnow().isoformat(),execution_id))
            db.execute("INSERT INTO demo_reconciliation_events(execution_id,state,details_json,created_at) VALUES(?,?,?,?)",
                (execution_id,state,json.dumps(reconciliation or {}),utcnow().isoformat()))

    def get_action_execution(self,user_sub:str,execution_id:str) -> dict[str,Any]|None:
        with self._connect() as db: row=db.execute("SELECT * FROM demo_action_executions WHERE id=? AND user_sub=?",(execution_id,user_sub)).fetchone()
        if not row:return None
        result=dict(row)
        result["broker_response"]=json.loads(result.pop("broker_response_json"));result["reconciliation"]=json.loads(result.pop("reconciliation_json"))
        return result

    def recent_executions(self,user_sub:str,limit:int=20)->list[dict[str,Any]]:
        with self._connect() as db:
            actions=db.execute("SELECT id,action_type,state,target_id,error_category,created_at,completed_at FROM demo_action_executions WHERE user_sub=? ORDER BY created_at DESC LIMIT ?",(user_sub,limit)).fetchall()
            runs=db.execute("SELECT id,decision action_type,result_status state,broker_order_id target_id,NULL error_category,created_at,completed_at FROM autonomous_runs WHERE user_sub=? ORDER BY created_at DESC LIMIT ?",(user_sub,limit)).fetchall()
            decisions=db.execute("""SELECT id,'autonomous_decision' action_type,state,profile_ref target_id,
                CASE WHEN reason_codes_json='[]' THEN NULL ELSE reason_codes_json END error_category,created_at,completed_at
                FROM autonomous_decision_runs WHERE user_sub=? ORDER BY created_at DESC LIMIT ?""",(user_sub,limit)).fetchall()
        return sorted([dict(row) for row in [*actions,*runs,*decisions]],key=lambda x:x["created_at"],reverse=True)[:limit]

    def new_entries_since(self,user_sub:str,account_id:str,since_iso:str)->int:
        with self._connect() as db:
            return int(db.execute("""SELECT COUNT(*) count FROM autonomous_runs WHERE user_sub=? AND account_id=?
                AND decision='submit' AND result_status IN ('verified','unknown') AND created_at>=?""",(user_sub,account_id,since_iso)).fetchone()["count"])
