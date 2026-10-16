"""Two SQLite tables: conversation metadata and ordered messages/events/approvals.
No second trading ledger: approvals refer to existing immutable preview IDs.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from app.config.settings import settings

class AgentStateError(ValueError):
    pass

class AgentRepository:
    def __init__(self, db_path=None):
        self.path = Path(db_path or settings.sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS agent_conversations(
                id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, mode TEXT NOT NULL,
                profile_ref TEXT, created_at REAL NOT NULL, busy_until REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS agent_conversations_owner ON agent_conversations(user_sub,created_at DESC);
            CREATE TABLE IF NOT EXISTS agent_messages(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'complete',
                created_at REAL NOT NULL, expires_at REAL);
            CREATE INDEX IF NOT EXISTS agent_messages_thread ON agent_messages(conversation_id,seq);
            ''')

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    def create(self, user, mode, profile):
        key = 'chat_' + uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO agent_conversations(id,user_sub,mode,profile_ref,created_at) VALUES(?,?,?,?,?)',
                       (key,user,str(mode),profile,time.time()))
        return key

    def owned(self, user, key):
        with self.db() as db:
            row=db.execute('SELECT * FROM agent_conversations WHERE id=? AND user_sub=?',(key,user)).fetchone()
        if not row: raise AgentStateError('conversation_not_found')
        return dict(row)

    def list(self, user):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,mode,profile_ref,created_at FROM agent_conversations WHERE user_sub=? ORDER BY created_at DESC LIMIT 50',(user,))]

    def messages(self, user, key):
        self.owned(user,key)
        with self.db() as db:
            rows=db.execute('SELECT * FROM agent_messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 200',(key,)).fetchall()
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in reversed(rows)]

    def add(self, key, kind, payload, *, pending=False):
        ident='msg_'+uuid4().hex; now=time.time()
        with self.db() as db:
            db.execute('INSERT INTO agent_messages(id,conversation_id,kind,payload,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?)',
                       (ident,key,kind,json.dumps(payload,default=str),'pending' if pending else 'complete',now,now+60 if pending else None))
        return ident

    def claim_turn(self,user,key):
        self.owned(user,key)
        with self.db() as db:
            changed=db.execute('UPDATE agent_conversations SET busy_until=? WHERE id=? AND user_sub=? AND busy_until<?',(time.time()+300,key,user,time.time())).rowcount
        if not changed:raise AgentStateError('conversation_busy')

    def release(self,key):
        with self.db() as db:db.execute('UPDATE agent_conversations SET busy_until=0 WHERE id=?',(key,))

    def claim_approval(self,user,key,approval):
        self.owned(user,key)
        with self.db() as db:
            changed=db.execute("UPDATE agent_messages SET state='claimed' WHERE id=? AND conversation_id=? AND kind='approval' AND state='pending' AND expires_at>?",(approval,key,time.time())).rowcount
            row=db.execute('SELECT payload FROM agent_messages WHERE id=? AND conversation_id=?',(approval,key)).fetchone()
        if not changed or not row:raise AgentStateError('approval_expired_or_used')
        return json.loads(row['payload'])

    def finish_approval(self,key,approval,result):
        with self.db() as db:
            # A failed or unknown broker result is never blindly retried by the assistant.
            db.execute("UPDATE agent_messages SET state='complete' WHERE id=? AND conversation_id=? AND state='claimed'",(approval,key))
        self.add(key,'execution',result)
