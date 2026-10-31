"""Atomic per-position write claims. Unconfirmed writes never expire or retry themselves."""
import math
import json
import sqlite3
from uuid import uuid4
from app.storage.database import connect, storage_path, using_postgres
from app.storage.execution import utcnow

TABLE_SQL="""CREATE TABLE IF NOT EXISTS position_management_state (
    user_sub TEXT NOT NULL, profile_ref TEXT NOT NULL, position_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'idle' CHECK(state IN ('idle','dispatching','unknown','closed')),
    token TEXT, desired_stop REAL, action_id TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY(user_sub,profile_ref,position_id))"""

class PositionManagementRepository:
    def __init__(self,db_path=None):
        self.db_path=storage_path(db_path)
        if not using_postgres():
            with self._connect() as db:db.execute(TABLE_SQL)

    def _connect(self):
        db=connect(self.db_path);db.row_factory=sqlite3.Row;return db

    def state(self,user,profile,position):
        with self._connect() as db:
            row=db.execute("SELECT * FROM position_management_state WHERE user_sub=? AND profile_ref=? AND position_id=?",(user,profile,position)).fetchone()
        return dict(row) if row else None

    def summary(self,user,profile):
        with self._connect() as db:
            rows=db.execute("SELECT state,COUNT(*) count FROM position_management_state WHERE user_sub=? AND profile_ref=? GROUP BY state",(user,profile)).fetchall()
        counts={row["state"]:int(row["count"]) for row in rows}
        return {"positions_with_history":sum(counts.values()),"pending_updates":counts.get("dispatching",0),"unconfirmed_updates":counts.get("unknown",0)}

    def claim(self,preview,desired_stop):
        """Claim, immutable action preview and execution record commit together."""
        if not math.isfinite(float(desired_stop)) or desired_stop<=0:raise ValueError("A finite positive stop is required")
        user=preview['user_sub'];profile=preview['profile_ref'];position=preview['target_id'];now=utcnow().isoformat()
        token=uuid4().hex;action=f"exec_{token}"
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO position_management_state(user_sub,profile_ref,position_id,updated_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",(user,profile,position,now))
            changed=db.execute("""UPDATE position_management_state SET state='dispatching',token=?,desired_stop=?,action_id=?,updated_at=?
                WHERE user_sub=? AND profile_ref=? AND position_id=? AND state='idle'""",(token,desired_stop,action,now,user,profile,position)).rowcount
            if not changed:return None
            encoded={**preview,'target_json':json.dumps(preview['target_json'],separators=(',',':'))}
            db.execute("""INSERT INTO demo_action_previews(id,user_sub,action_type,profile_ref,account_record_id,connection_ref,
                connection_id,account_id,acc_num,account_alias,environment,server,base_url,demo_classification,target_id,
                target_json,status,expires_at,created_at) VALUES(:id,:user_sub,:action_type,:profile_ref,:account_record_id,:connection_ref,
                :connection_id,:account_id,:acc_num,:account_alias,:environment,:server,:base_url,:demo_classification,:target_id,
                :target_json,:status,:expires_at,:created_at)""",encoded)
            db.execute("""INSERT INTO demo_action_executions(id,preview_id,user_sub,idempotency_key,action_type,target_id,state,created_at)
                VALUES(?,?,?,?,'update_stop',?,'claimed',?)""",(action,preview['id'],user,f'management:{token}',position,now))
        return {'user_sub':user,'profile_ref':profile,'position_id':position,'token':token,'action_id':action,'desired_stop':desired_stop}

    def finish(self,claim,*,outcome,reconciliation=None,error=None):
        state='idle' if outcome in {'verified','rejected'} else 'closed' if outcome=='closed' else 'unknown'
        now=utcnow().isoformat();details=json.dumps(reconciliation or {},separators=(',',':'))
        with self._connect() as db:
            changed=db.execute("""UPDATE position_management_state SET state=?,updated_at=?
                WHERE user_sub=? AND profile_ref=? AND position_id=? AND token=?""",
                (state,now,claim['user_sub'],claim['profile_ref'],claim['position_id'],claim['token'])).rowcount
            if not changed:return False
            db.execute("UPDATE demo_action_executions SET state=?,reconciliation_json=?,error_category=?,completed_at=? WHERE id=? AND user_sub=?",
                (outcome,details,error,now,claim['action_id'],claim['user_sub']))
            db.execute("INSERT INTO demo_reconciliation_events(execution_id,state,details_json,created_at) VALUES(?,?,?,?)",(claim['action_id'],outcome,details,now))
        return True
