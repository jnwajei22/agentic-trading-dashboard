"""Owner-scoped operations journal. Broker secrets and model prompts never enter it."""
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.storage.database import connect, storage_path, using_postgres

TABLE_SQL = """CREATE TABLE IF NOT EXISTS workspace_events (
    id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL, subject_ref TEXT, profile_ref TEXT,
    account_ref TEXT, summary TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}')"""


def ensure_events(db):
    if not using_postgres():
        db.execute(TABLE_SQL)
        db.execute("CREATE INDEX IF NOT EXISTS workspace_events_owner_time ON workspace_events(user_sub,occurred_at DESC)")


def record_event(db, user_sub, event_type, summary, *, subject_ref=None, profile_ref=None,
                 account_ref=None, details=None):
    ensure_events(db)
    allowed = {"name", "enabled", "timezone", "schedule_type", "changed_fields", "execution_mode", "decision_provider"}
    safe = {key: value for key, value in (details or {}).items() if key in allowed}
    db.execute("""INSERT INTO workspace_events(id,user_sub,event_type,occurred_at,subject_ref,
        profile_ref,account_ref,summary,details_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (f"event_{uuid4().hex}", user_sub, event_type, datetime.now(timezone.utc).isoformat(),
         subject_ref, profile_ref, account_ref, summary[:300], json.dumps(safe)))


class WorkspaceEventRepository:
    def __init__(self, db_path=None):
        self.db_path = storage_path(db_path)

    def list(self, user_sub, limit=500):
        with connect(self.db_path) as db:
            db.row_factory=sqlite3.Row
            ensure_events(db)
            rows = db.execute("SELECT * FROM workspace_events WHERE user_sub=? ORDER BY occurred_at DESC LIMIT ?",
                              (user_sub, min(max(int(limit), 1), 2000))).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]
