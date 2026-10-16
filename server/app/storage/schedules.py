from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.config.settings import settings

logger=logging.getLogger(__name__)


def utcnow()->datetime:return datetime.now(timezone.utc)


class ScheduleStorageError(RuntimeError):pass


class ScheduleRepository:
    """SQLite durable scheduler state. BEGIN IMMEDIATE is the single-process/dev lease fallback."""
    def __init__(self,db_path:str|Path|None=None)->None:
        self.db_path=Path(db_path or settings.sqlite_path);self.db_path.parent.mkdir(parents=True,exist_ok=True);self._initialize()

    def _connect(self)->sqlite3.Connection:
        db=sqlite3.connect(self.db_path,timeout=30);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db

    def _initialize(self)->None:
        with self._connect() as db:db.executescript("""
            CREATE TABLE IF NOT EXISTS autonomous_schedules(
                id TEXT PRIMARY KEY,user_sub TEXT NOT NULL,profile_ref TEXT NOT NULL,
                timezone TEXT NOT NULL,schedule_type TEXT NOT NULL CHECK(schedule_type='daily_times'),
                expression_json TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,
                next_run_at TEXT,last_run_at TEXT,last_run_status TEXT,
                misfire_policy TEXT NOT NULL DEFAULT 'skip' CHECK(misfire_policy='skip'),
                maximum_lateness_seconds INTEGER NOT NULL DEFAULT 600,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,disabled_at TEXT,
                UNIQUE(user_sub,profile_ref)
            );
            CREATE INDEX IF NOT EXISTS due_autonomous_schedules ON autonomous_schedules(enabled,next_run_at);
            CREATE TABLE IF NOT EXISTS autonomous_schedule_dispatches(
                id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL,user_sub TEXT NOT NULL,profile_ref TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,run_key TEXT NOT NULL,state TEXT NOT NULL,outcome TEXT,
                run_id TEXT,retry_count INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,
                safe_retry INTEGER NOT NULL DEFAULT 0,reason_code TEXT,
                lease_owner TEXT,lease_expires_at TEXT,claimed_at TEXT,finished_at TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                UNIQUE(schedule_id,scheduled_for),UNIQUE(user_sub,profile_ref,run_key),
                FOREIGN KEY(schedule_id) REFERENCES autonomous_schedules(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS due_autonomous_dispatches ON autonomous_schedule_dispatches(state,next_retry_at,lease_expires_at);
            CREATE TABLE IF NOT EXISTS autonomous_worker_heartbeats(
                worker_id TEXT PRIMARY KEY,status TEXT NOT NULL,started_at TEXT NOT NULL,last_heartbeat_at TEXT NOT NULL,
                pid INTEGER,details_json TEXT NOT NULL DEFAULT '{}'
            );
        """)

    @staticmethod
    def _decode(row:sqlite3.Row|None)->dict[str,Any]:
        if row is None:raise KeyError("record not found")
        result=dict(row)
        for key in ("expression_json","summary_json","details_json"):
            if key in result:result[key.removesuffix("_json")]=json.loads(result.pop(key))
        for key in ("enabled","safe_retry"):
            if key in result:result[key]=bool(result[key])
        return result

    def upsert_schedule(self,user_sub:str,profile_ref:str,*,timezone_name:str,local_times:list[str],enabled:bool,
                        next_run_at:str|None,maximum_lateness_seconds:int=600,
                        recurrence:dict[str,Any]|None=None)->dict[str,Any]:
        payload=recurrence or {"type":"daily","times":local_times}
        # Keep times at the top level for legacy workers/readers during the compatibility window.
        expression=json.dumps({"times":local_times,"recurrence":payload},separators=(",",":"))
        now=utcnow().isoformat()
        with self._connect() as db:
            existing=db.execute("SELECT id,created_at FROM autonomous_schedules WHERE user_sub=? AND profile_ref=?",(user_sub,profile_ref)).fetchone()
            schedule_id=existing["id"] if existing else f"schedule_{uuid4().hex}"
            created=existing["created_at"] if existing else now
            db.execute("""INSERT INTO autonomous_schedules(id,user_sub,profile_ref,timezone,schedule_type,expression_json,
                enabled,next_run_at,maximum_lateness_seconds,created_at,updated_at,disabled_at)
                VALUES(?,?,?,?,'daily_times',?,?,?,?,?,?,?) ON CONFLICT(user_sub,profile_ref) DO UPDATE SET
                timezone=excluded.timezone,expression_json=excluded.expression_json,enabled=excluded.enabled,
                next_run_at=excluded.next_run_at,maximum_lateness_seconds=excluded.maximum_lateness_seconds,
                updated_at=excluded.updated_at,disabled_at=excluded.disabled_at""",
                (schedule_id,user_sub,profile_ref,timezone_name,expression,enabled,next_run_at,maximum_lateness_seconds,
                 created,now,None if enabled else now))
        return self.get_schedule(user_sub,schedule_id) or {}

    def get_schedule(self,user_sub:str,schedule_id:str)->dict[str,Any]|None:
        with self._connect() as db:row=db.execute("SELECT * FROM autonomous_schedules WHERE id=? AND user_sub=?",(schedule_id,user_sub)).fetchone()
        return self._decode(row) if row else None

    def get_profile_schedule(self,user_sub:str,profile_ref:str)->dict[str,Any]|None:
        with self._connect() as db:row=db.execute("SELECT * FROM autonomous_schedules WHERE user_sub=? AND profile_ref=?",(user_sub,profile_ref)).fetchone()
        return self._decode(row) if row else None

    def list_schedules(self,user_sub:str)->list[dict[str,Any]]:
        with self._connect() as db:rows=db.execute("SELECT * FROM autonomous_schedules WHERE user_sub=? ORDER BY created_at",(user_sub,)).fetchall()
        return [self._decode(row) for row in rows]

    def delete_schedule(self,user_sub:str,schedule_id:str)->bool:
        with self._connect() as db:return db.execute("DELETE FROM autonomous_schedules WHERE id=? AND user_sub=?",(schedule_id,user_sub)).rowcount==1

    def disable_profile_schedule(self,user_sub:str,profile_ref:str)->bool:
        now=utcnow().isoformat()
        with self._connect() as db:
            return db.execute("""UPDATE autonomous_schedules SET enabled=0,next_run_at=NULL,
                disabled_at=?,updated_at=? WHERE user_sub=? AND profile_ref=?""",
                (now,now,user_sub,profile_ref)).rowcount > 0

    def set_enabled(self,user_sub:str,schedule_id:str,enabled:bool,next_run_at:str|None)->bool:
        now=utcnow().isoformat()
        with self._connect() as db:
            return db.execute("""UPDATE autonomous_schedules SET enabled=?,next_run_at=?,disabled_at=?,updated_at=?
                WHERE id=? AND user_sub=?""",(enabled,next_run_at,None if enabled else now,now,schedule_id,user_sub)).rowcount==1

    def claim_due(self,*,worker_id:str,now:datetime,lease_seconds:int,limit:int,
                  next_run:Callable[[dict[str,Any],datetime],datetime])->list[dict[str,Any]]:
        now_iso=now.isoformat();lease=(now+timedelta(seconds=lease_seconds)).isoformat();claimed=[]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            retry_rows=db.execute("""SELECT * FROM autonomous_schedule_dispatches WHERE
                (state='retry_wait' AND next_retry_at<=?) OR
                (state IN ('claimed','running') AND lease_expires_at<=?) ORDER BY COALESCE(next_retry_at,scheduled_for) LIMIT ?""",
                (now_iso,now_iso,limit)).fetchall()
            for row in retry_rows:
                db.execute("""UPDATE autonomous_schedule_dispatches SET state='claimed',lease_owner=?,lease_expires_at=?,
                    claimed_at=?,updated_at=? WHERE id=?""",(worker_id,lease,now_iso,now_iso,row["id"]))
                claimed.append(self._decode(db.execute("SELECT * FROM autonomous_schedule_dispatches WHERE id=?",(row["id"],)).fetchone()))
                logger.info("autonomous_schedule_reclaimed worker_id=%s dispatch_id=%s profile_ref=%s retry_count=%s",
                    worker_id,row["id"],row["profile_ref"],row["retry_count"])
            remaining=max(0,limit-len(claimed))
            schedules=db.execute("""SELECT * FROM autonomous_schedules WHERE enabled=1 AND next_run_at IS NOT NULL
                AND next_run_at<=? ORDER BY next_run_at LIMIT ?""",(now_iso,remaining)).fetchall()
            for raw in schedules:
                schedule=self._decode(raw);scheduled=datetime.fromisoformat(schedule["next_run_at"])
                lateness=(now-scheduled).total_seconds();stale=lateness>schedule["maximum_lateness_seconds"]
                following=next_run(schedule,now if stale else scheduled).isoformat()
                dispatch_id=f"dispatch_{uuid4().hex}";run_key=f"scheduled:{schedule['profile_ref']}:{scheduled.astimezone(timezone.utc).isoformat()}"
                state="misfired" if stale else "claimed";reason="maximum_lateness_exceeded" if stale else None
                try:db.execute("""INSERT INTO autonomous_schedule_dispatches(id,schedule_id,user_sub,profile_ref,scheduled_for,
                    run_key,state,reason_code,lease_owner,lease_expires_at,claimed_at,finished_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(dispatch_id,schedule["id"],schedule["user_sub"],schedule["profile_ref"],
                    scheduled.isoformat(),run_key,state,reason,worker_id if not stale else None,lease if not stale else None,
                    now_iso if not stale else None,now_iso if stale else None,now_iso,now_iso))
                except sqlite3.IntegrityError:
                    logger.info("autonomous_schedule_duplicate_suppressed schedule_id=%s profile_ref=%s scheduled_for=%s",
                        schedule["id"],schedule["profile_ref"],scheduled.isoformat())
                db.execute("""UPDATE autonomous_schedules SET next_run_at=?,last_run_at=?,last_run_status=?,updated_at=? WHERE id=?""",
                    (following,scheduled.isoformat(),state,now_iso,schedule["id"]))
                if not stale:
                    row=db.execute("SELECT * FROM autonomous_schedule_dispatches WHERE schedule_id=? AND scheduled_for=?",
                        (schedule["id"],scheduled.isoformat())).fetchone()
                    if row and row["lease_owner"]==worker_id:claimed.append(self._decode(row))
                    logger.info("autonomous_schedule_claimed worker_id=%s schedule_id=%s profile_ref=%s scheduled_for=%s",
                        worker_id,schedule["id"],schedule["profile_ref"],scheduled.isoformat())
                else:
                    logger.warning("autonomous_schedule_misfired schedule_id=%s profile_ref=%s scheduled_for=%s lateness_seconds=%s reason=maximum_lateness_exceeded",
                        schedule["id"],schedule["profile_ref"],scheduled.isoformat(),int(lateness))
        return claimed

    def mark_running(self,dispatch_id:str,worker_id:str,lease_seconds:int)->bool:
        now=utcnow();
        with self._connect() as db:return db.execute("""UPDATE autonomous_schedule_dispatches SET state='running',
            lease_expires_at=?,updated_at=? WHERE id=? AND lease_owner=? AND state='claimed'""",
            ((now+timedelta(seconds=lease_seconds)).isoformat(),now.isoformat(),dispatch_id,worker_id)).rowcount==1

    def finish_dispatch(self,dispatch_id:str,*,state:str,outcome:str|None,run_id:str|None,reason_code:str|None,
                        summary:dict[str,Any],safe_retry:bool=False,next_retry_at:str|None=None,retry_count:int|None=None)->None:
        now=utcnow().isoformat();updates={"state":state,"outcome":outcome,"run_id":run_id,"reason_code":reason_code,
            "summary_json":json.dumps(summary,separators=(",",":"),sort_keys=True),"safe_retry":safe_retry,
            "next_retry_at":next_retry_at,"lease_owner":None,"lease_expires_at":None,"updated_at":now,
            "finished_at":None if state=="retry_wait" else now}
        if retry_count is not None:updates["retry_count"]=retry_count
        assignments=",".join(f"{key}=:{key}" for key in updates)
        with self._connect() as db:
            db.execute(f"UPDATE autonomous_schedule_dispatches SET {assignments} WHERE id=:id",{**updates,"id":dispatch_id})
            row=db.execute("SELECT schedule_id,scheduled_for FROM autonomous_schedule_dispatches WHERE id=?",(dispatch_id,)).fetchone()
            if row:db.execute("UPDATE autonomous_schedules SET last_run_at=?,last_run_status=?,updated_at=? WHERE id=?",
                (row["scheduled_for"],outcome or state,now,row["schedule_id"]))

    def request_safe_retry(self,user_sub:str,dispatch_id:str,when:str)->bool:
        with self._connect() as db:
            return db.execute("""UPDATE autonomous_schedule_dispatches SET state='retry_wait',next_retry_at=?,finished_at=NULL,
                updated_at=? WHERE id=? AND user_sub=? AND safe_retry=1 AND state IN ('retry_exhausted','error')""",
                (when,utcnow().isoformat(),dispatch_id,user_sub)).rowcount==1

    def list_dispatches(self,user_sub:str,limit:int=50,profile_ref:str|None=None)->list[dict[str,Any]]:
        with self._connect() as db:
            if profile_ref:rows=db.execute("SELECT * FROM autonomous_schedule_dispatches WHERE user_sub=? AND profile_ref=? ORDER BY scheduled_for DESC LIMIT ?",(user_sub,profile_ref,limit)).fetchall()
            else:rows=db.execute("SELECT * FROM autonomous_schedule_dispatches WHERE user_sub=? ORDER BY scheduled_for DESC LIMIT ?",(user_sub,limit)).fetchall()
        return [self._decode(row) for row in rows]

    def heartbeat(self,worker_id:str,status:str,details:dict[str,Any]|None=None)->None:
        now=utcnow().isoformat()
        with self._connect() as db:db.execute("""INSERT INTO autonomous_worker_heartbeats(worker_id,status,started_at,last_heartbeat_at,pid,details_json)
            VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET status=excluded.status,last_heartbeat_at=excluded.last_heartbeat_at,
            details_json=excluded.details_json""",(worker_id,status,now,now,os.getpid(),json.dumps(details or {},separators=(",",":"))))

    def worker_health(self)->dict[str,Any]:
        with self._connect() as db:rows=db.execute("SELECT * FROM autonomous_worker_heartbeats ORDER BY last_heartbeat_at DESC").fetchall()
        workers=[self._decode(row) for row in rows];now=utcnow()
        for item in workers:
            item["healthy"]=item["status"]=="running" and (now-datetime.fromisoformat(item["last_heartbeat_at"])).total_seconds()<=settings.autonomous_scheduler_heartbeat_stale_seconds
        return {"status":"healthy" if any(item["healthy"] for item in workers) else "unavailable","workers":workers}
