from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import settings
from app.config.crypto import SecretDecryptionError, VersionedSecretCipher
from app.models.execution_profile_v2 import ExecutionProfileV2, deep_merge, migrate_legacy_profile


class BrokerStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerConnection:
    connection_id: str
    base_url: str
    username: str
    password: str = field(repr=False)
    server: str = ""
    account_id: str | None = None
    account_number: str | None = None
    environment: str = "unknown"
    label: str | None = None
    enabled: bool = True
    connection_ref: str = ""


def infer_tradelocker_environment(base_url: str, explicit: str | None = None) -> str:
    if explicit and explicit.lower() in {"demo", "live"}:
        return explicit.lower()
    lowered = base_url.lower()
    if "live.tradelocker" in lowered:
        return "live"
    if "demo.tradelocker" in lowered:
        return "demo"
    configured = settings.tradelocker_environment.lower()
    return configured if configured in {"demo", "live"} else "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ref(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class BrokerRepository:
    def __init__(self, db_path: str | Path | None = None, secret: str | None = None) -> None:
        self.db_path = Path(db_path or settings.sqlite_path)
        self.secret = secret if secret is not None else settings.broker_secret_key
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            migrated_single_connection = False
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, auth0_sub TEXT NOT NULL UNIQUE,
                email TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            existing = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='broker_connections'"
            ).fetchone()
            if existing and "user_id INTEGER NOT NULL UNIQUE" in (existing["sql"] or ""):
                migrated_single_connection = True
                old_columns = {r["name"] for r in db.execute("PRAGMA table_info(broker_connections)")}
                if "environment" not in old_columns:
                    db.execute("ALTER TABLE broker_connections ADD COLUMN environment TEXT NOT NULL DEFAULT 'demo'")
                    db.execute("UPDATE broker_connections SET environment='live' WHERE lower(base_url) LIKE '%live.tradelocker%'")
                db.execute("PRAGMA foreign_keys = OFF")
                db.execute("ALTER TABLE broker_connections RENAME TO broker_connections_legacy")
                self._create_connections(db)
                db.execute("""INSERT INTO broker_connections(
                    id,user_id,public_id,provider,base_url,username,password_encrypted,server,
                    account_id,account_number,environment,label,status,created_at,updated_at)
                    SELECT id,user_id,'conn_' || lower(hex(randomblob(16))),provider,base_url,
                    username,password_encrypted,server,account_id,account_number,environment,
                    server,'active',created_at,updated_at FROM broker_connections_legacy""")
                db.execute("DROP TABLE broker_connections_legacy")
                db.execute("PRAGMA foreign_keys = ON")
            else:
                self._create_connections(db)
            connection_columns={row["name"] for row in db.execute("PRAGMA table_info(broker_connections)")}
            if "needs_discovery_refresh" not in connection_columns:
                db.execute("ALTER TABLE broker_connections ADD COLUMN needs_discovery_refresh INTEGER NOT NULL DEFAULT 1")
            if "discovery_version" not in connection_columns:
                db.execute("ALTER TABLE broker_connections ADD COLUMN discovery_version INTEGER NOT NULL DEFAULT 0")
            if "encryption_key_version" not in connection_columns:
                db.execute("ALTER TABLE broker_connections ADD COLUMN encryption_key_version TEXT NOT NULL DEFAULT 'v1'")
            db.execute("""CREATE TABLE IF NOT EXISTS credential_key_migrations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,connection_public_id TEXT NOT NULL,
                from_version TEXT NOT NULL,to_version TEXT NOT NULL,migrated_at TEXT NOT NULL)""")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS broker_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    connection_id INTEGER NOT NULL,
                    broker_account_id TEXT NOT NULL,
                    acc_num TEXT NOT NULL,
                    account_alias TEXT NOT NULL COLLATE NOCASE,
                    nickname TEXT,
                    account_name TEXT,
                    currency TEXT,
                    environment TEXT NOT NULL CHECK(environment IN ('demo','live','unknown')),
                    is_demo INTEGER,
                    broker_active INTEGER NOT NULL DEFAULT 1,
                    locally_enabled INTEGER NOT NULL DEFAULT 1,
                    available INTEGER NOT NULL DEFAULT 1,
                    is_default_analysis INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_discovered_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL,
                    unavailable_since TEXT,
                    UNIQUE(connection_id, broker_account_id, acc_num),
                    UNIQUE(user_id, account_alias),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(connection_id) REFERENCES broker_connections(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_default_analysis_account
                    ON broker_accounts(user_id) WHERE is_default_analysis = 1;
                CREATE TABLE IF NOT EXISTS strategy_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, version TEXT NOT NULL, description TEXT,
                    config_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    UNIQUE(name, version));
                CREATE TABLE IF NOT EXISTS execution_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL, broker_account_id INTEGER NOT NULL,
                    strategy_template_id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                    execution_mode TEXT NOT NULL DEFAULT 'read_only'
                        CHECK(execution_mode IN ('read_only','demo_manual','demo_autonomous','disabled')),
                    risk_json TEXT NOT NULL DEFAULT '{}', allowed_instruments_json TEXT NOT NULL DEFAULT '[]',
                    session_rules_json TEXT NOT NULL DEFAULT '{}', news_filter_enabled INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(broker_account_id) REFERENCES broker_accounts(id) ON DELETE RESTRICT,
                    FOREIGN KEY(strategy_template_id) REFERENCES strategy_templates(id) ON DELETE RESTRICT);
            """)
            account_columns={row["name"] for row in db.execute("PRAGMA table_info(broker_accounts)")}
            if "nickname" not in account_columns:
                db.execute("ALTER TABLE broker_accounts ADD COLUMN nickname TEXT")
            profile_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='execution_profiles'").fetchone()
            if profile_sql and "demo_enabled" in (profile_sql["sql"] or ""):
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("ALTER TABLE execution_profiles RENAME TO execution_profiles_legacy_mode")
                db.execute("""CREATE TABLE execution_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL, broker_account_id INTEGER NOT NULL,
                    strategy_template_id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                    execution_mode TEXT NOT NULL DEFAULT 'read_only' CHECK(execution_mode IN ('read_only','demo_manual','demo_autonomous','disabled')),
                    risk_json TEXT NOT NULL DEFAULT '{}', allowed_instruments_json TEXT NOT NULL DEFAULT '[]',
                    session_rules_json TEXT NOT NULL DEFAULT '{}', news_filter_enabled INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_id,name), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(broker_account_id) REFERENCES broker_accounts(id) ON DELETE RESTRICT,
                    FOREIGN KEY(strategy_template_id) REFERENCES strategy_templates(id) ON DELETE RESTRICT)""")
                db.execute("""INSERT INTO execution_profiles SELECT id,public_id,user_id,broker_account_id,strategy_template_id,name,
                    CASE execution_mode WHEN 'demo_enabled' THEN 'demo_manual' ELSE execution_mode END,risk_json,
                    allowed_instruments_json,session_rules_json,news_filter_enabled,enabled,created_at,updated_at
                    FROM execution_profiles_legacy_mode""")
                db.execute("DROP TABLE execution_profiles_legacy_mode")
                db.execute("PRAGMA foreign_keys=ON")
            profile_columns={row["name"] for row in db.execute("PRAGMA table_info(execution_profiles)")}
            autonomous_columns={
                "autonomous_armed":"INTEGER NOT NULL DEFAULT 0",
                "armed_at":"TEXT",
                "armed_until":"TEXT",
                "armed_by_user":"TEXT",
                "decision_provider":"TEXT NOT NULL DEFAULT 'no_trade'",
                "model_identifier":"TEXT",
                "minimum_confidence":"REAL NOT NULL DEFAULT 0.70",
                "allowed_sessions_json":"TEXT NOT NULL DEFAULT '[\"london\",\"new_york\",\"overlap\"]'",
                "schedule_ref":"TEXT",
                "autonomous_shadow_mode":"INTEGER NOT NULL DEFAULT 1",
                "cooldown_minutes_after_loss":"INTEGER NOT NULL DEFAULT 60",
            }
            for column,declaration in autonomous_columns.items():
                if column not in profile_columns:
                    db.execute(f"ALTER TABLE execution_profiles ADD COLUMN {column} {declaration}")
            if "profile_v2_json" not in profile_columns:
                db.execute("ALTER TABLE execution_profiles ADD COLUMN profile_v2_json TEXT")
            now = _now()
            db.execute("""INSERT OR IGNORE INTO strategy_templates
                (public_id,name,version,description,config_json,created_at)
                VALUES ('strategy_hourly_forex_v1','hourly_forex','1','Built-in hourly forex template','{}',?)""", (now,))
            db.execute("""INSERT OR IGNORE INTO strategy_templates
                (public_id,name,version,description,config_json,created_at) VALUES
                ('strategy_ai_forex_confluence_v1','ai_forex_confluence','1',
                 'Bounded multi-timeframe AI forex confluence strategy',?,?)""",
                (json.dumps({"pairs":["EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD"],
                 "timeframes":["1d","4h","1h","15m"],"required_indicators":["sma20","sma50","rsi14","macd","atr14","structure","support_resistance"],
                 "required_macro_series":["policy_rates","inflation","labor","growth"],"demo_only":True}),now))
            # Preserve legacy selections as durable accounts without changing credentials.
            legacy = db.execute("""SELECT id,user_id,server,account_id,account_number,environment
                FROM broker_connections WHERE account_id IS NOT NULL AND account_number IS NOT NULL""").fetchall()
            for row in legacy:
                found = db.execute("SELECT id FROM broker_accounts WHERE connection_id=? AND broker_account_id=? AND acc_num=?",
                                   (row["id"], row["account_id"], row["account_number"])).fetchone()
                if not found:
                    alias = self._unique_alias(db, row["user_id"], f"{row['server']}-{row['environment']}-{row['account_number']}")
                    has_default = db.execute("SELECT 1 FROM broker_accounts WHERE user_id=? AND is_default_analysis=1", (row["user_id"],)).fetchone()
                    db.execute("""INSERT INTO broker_accounts(public_id,user_id,connection_id,broker_account_id,acc_num,
                        account_alias,environment,is_demo,is_default_analysis,first_discovered_at,last_verified_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (_ref("acct"),row["user_id"],row["id"],row["account_id"],row["account_number"],
                        alias,row["environment"],1 if row["environment"] == "demo" else 0 if row["environment"] == "live" else None,
                        0 if has_default else 1,now,now))
                    found = db.execute("SELECT id,account_alias FROM broker_accounts WHERE connection_id=? AND broker_account_id=? AND acc_num=?",
                        (row["id"],row["account_id"],row["account_number"])).fetchone()
                if migrated_single_connection and found and not db.execute(
                    "SELECT 1 FROM execution_profiles WHERE broker_account_id=?", (found["id"],)
                ).fetchone():
                    template = db.execute("SELECT id FROM strategy_templates WHERE public_id='strategy_hourly_forex_v1'").fetchone()
                    alias_row = db.execute("SELECT account_alias FROM broker_accounts WHERE id=?",(found["id"],)).fetchone()
                    db.execute("""INSERT INTO execution_profiles(public_id,user_id,broker_account_id,strategy_template_id,name,
                        execution_mode,created_at,updated_at) VALUES(?,?,?,?,?,'read_only',?,?)""",
                        (_ref("profile"),row["user_id"],found["id"],template["id"],f"{alias_row['account_alias']}-hourly",now,now))

    @staticmethod
    def _create_connections(db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS broker_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            public_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL CHECK(provider='tradelocker'),
            base_url TEXT NOT NULL, username TEXT NOT NULL, password_encrypted BLOB NOT NULL,
            server TEXT NOT NULL, account_id TEXT, account_number TEXT,
            environment TEXT NOT NULL DEFAULT 'unknown' CHECK(environment IN ('demo','live','unknown')),
            label TEXT, broker_name TEXT NOT NULL DEFAULT 'TradeLocker', status TEXT NOT NULL DEFAULT 'active',
            last_authenticated_at TEXT, last_discovery_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            needs_discovery_refresh INTEGER NOT NULL DEFAULT 1, discovery_version INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")

    def _fernet(self) -> Fernet:
        if not self.secret:
            raise BrokerStorageError("BROKER_SECRET_KEY is not configured.")
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(self.secret.encode()).digest()))

    def _cipher(self) -> VersionedSecretCipher:
        try: keys = json.loads(settings.broker_secret_keys_json or "{}")
        except json.JSONDecodeError: keys = {}
        if self.secret: keys.setdefault("v1", self.secret)
        active = settings.active_encryption_key_version if settings.active_encryption_key_version in keys else "v1"
        if not keys: raise BrokerStorageError("BROKER_SECRET_KEY is not configured.")
        return VersionedSecretCipher(keys, active)

    def ensure_user(self, auth0_sub: str, email: str | None = None) -> int:
        now = _now()
        with self._connect() as db:
            db.execute("""INSERT INTO users(auth0_sub,email,created_at,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(auth0_sub) DO UPDATE SET email=COALESCE(excluded.email,users.email),updated_at=excluded.updated_at""",
                (auth0_sub,email,now,now))
            return int(db.execute("SELECT id FROM users WHERE auth0_sub=?", (auth0_sub,)).fetchone()["id"])

    def save_connection(self, auth0_sub: str, *, base_url: str, username: str, password: str,
                        server: str, environment: str | None = None, email: str | None = None,
                        connection_ref: str | None = None, label: str | None = None,
                        create_new: bool = False) -> BrokerConnection:
        user_id = self.ensure_user(auth0_sub, email)
        env = infer_tradelocker_environment(base_url, environment)
        encrypted, key_version = self._cipher().encrypt(password)
        now = _now()
        with self._connect() as db:
            target = None
            if connection_ref:
                target = db.execute("SELECT id FROM broker_connections WHERE user_id=? AND public_id=?", (user_id,connection_ref)).fetchone()
                if not target:
                    raise BrokerStorageError("TradeLocker connection was not found.")
            elif not create_new:
                rows = db.execute("SELECT id FROM broker_connections WHERE user_id=?", (user_id,)).fetchall()
                target = rows[0] if len(rows) == 1 else None
                if len(rows) > 1:
                    raise BrokerStorageError("Choose the connection to reauthenticate.")
            if target:
                db.execute("""UPDATE broker_connections SET base_url=?,username=?,password_encrypted=?,encryption_key_version=?,server=?,environment=?,
                    label=COALESCE(?,label),status='active',needs_discovery_refresh=1,last_authenticated_at=?,updated_at=? WHERE id=? AND user_id=?""",
                    (base_url.rstrip('/'),username,encrypted,key_version,server,env,label,now,now,target["id"],user_id))
                connection_id = int(target["id"])
            else:
                cur = db.execute("""INSERT INTO broker_connections(user_id,public_id,provider,base_url,username,password_encrypted,
                    encryption_key_version,server,environment,label,status,last_authenticated_at,created_at,updated_at)
                    VALUES(?,?,'tradelocker',?,?,?,?,?,?,?,'active',?,?,?)""",
                    (user_id,_ref("conn"),base_url.rstrip('/'),username,encrypted,key_version,server,env,label or server,now,now,now))
                connection_id = int(cur.lastrowid)
        return self.get_connection_by_id(auth0_sub, str(connection_id))

    def _row_connection(self, row: sqlite3.Row) -> BrokerConnection:
        try:
            version = row["encryption_key_version"] if "encryption_key_version" in row.keys() else "v1"
            password = self._cipher().decrypt(row["password_encrypted"], version)
        except (InvalidToken, SecretDecryptionError):
            raise BrokerStorageError("Stored broker credentials cannot be decrypted.") from None
        return BrokerConnection(connection_id=str(row["id"]),base_url=row["base_url"],username=row["username"],password=password,
            server=row["server"],account_id=row["account_id"],account_number=row["account_number"],environment=row["environment"],
            label=row["label"],enabled=row["status"] == "active",connection_ref=row["public_id"])

    def reencrypt_credentials(self, limit: int = 100) -> int:
        cipher = self._cipher(); migrated = 0
        with self._connect() as db:
            rows = db.execute("SELECT id,public_id,password_encrypted,encryption_key_version FROM broker_connections WHERE encryption_key_version<>? LIMIT ?",
                (cipher.active_version, limit)).fetchall()
            for row in rows:
                try: encrypted, version = cipher.reencrypt(row["password_encrypted"], row["encryption_key_version"])
                except SecretDecryptionError: continue
                db.execute("UPDATE broker_connections SET password_encrypted=?,encryption_key_version=?,updated_at=? WHERE id=?",
                    (encrypted, version, _now(), row["id"]))
                db.execute("INSERT INTO credential_key_migrations(connection_public_id,from_version,to_version,migrated_at) VALUES(?,?,?,?)",
                    (row["public_id"], row["encryption_key_version"], version, _now()))
                migrated += 1
        return migrated

    def get_connection_by_id(self, auth0_sub: str, connection_id: str) -> BrokerConnection:
        with self._connect() as db:
            row = db.execute("""SELECT b.* FROM broker_connections b JOIN users u ON u.id=b.user_id
                WHERE u.auth0_sub=? AND CAST(b.id AS TEXT)=?""", (auth0_sub,connection_id)).fetchone()
        if not row: raise BrokerStorageError("TradeLocker connection was not found.")
        return self._row_connection(row)

    def get_connection(self, auth0_sub: str, connection_ref: str | None = None) -> BrokerConnection | None:
        with self._connect() as db:
            args: list[Any] = [auth0_sub]
            where = "u.auth0_sub=? AND b.provider='tradelocker' AND b.status='active'"
            if connection_ref:
                where += " AND b.public_id=?"; args.append(connection_ref)
            row = db.execute(f"""SELECT b.* FROM broker_connections b JOIN users u ON u.id=b.user_id
                LEFT JOIN broker_accounts a ON a.connection_id=b.id AND a.is_default_analysis=1
                WHERE {where} ORDER BY (a.id IS NOT NULL) DESC,b.id LIMIT 1""", args).fetchone()
        return self._row_connection(row) if row else None

    def list_connections(self, auth0_sub: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""SELECT b.public_id,b.label,b.broker_name,b.server,b.environment,b.status,
                b.last_authenticated_at,b.last_discovery_at,b.needs_discovery_refresh,b.discovery_version,COUNT(a.id) account_count,MAX(a.last_verified_at) last_verified_at,
                MAX(a.is_default_analysis) is_default
                FROM broker_connections b JOIN users u ON u.id=b.user_id LEFT JOIN broker_accounts a ON a.connection_id=b.id
                WHERE u.auth0_sub=? GROUP BY b.id ORDER BY b.id""", (auth0_sub,)).fetchall()
        return [{**dict(r), "connection_id": r["public_id"], "broker_name": r["server"],
                 "platform_name": "TradeLocker", "enabled": r["status"] == "active",
                 "is_default":bool(r["is_default"])} for r in rows]

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or "account"

    @classmethod
    def _unique_alias(cls, db: sqlite3.Connection, user_id: int, seed: str) -> str:
        base, n = cls._slug(seed), 1
        alias = base
        while db.execute("SELECT 1 FROM broker_accounts WHERE user_id=? AND account_alias=? COLLATE NOCASE", (user_id,alias)).fetchone():
            n += 1; alias = f"{base}-{n}"
        return alias

    def sync_accounts(self, auth0_sub: str, connection_id: str, payload: Any) -> list[dict[str, Any]]:
        accounts = payload.get("accounts", []) if isinstance(payload, dict) else payload
        if not isinstance(accounts, list): raise BrokerStorageError("TradeLocker account discovery payload is invalid.")
        now = _now()
        with self._connect() as db:
            owner = db.execute("SELECT u.id user_id,b.id,b.server,b.environment FROM broker_connections b JOIN users u ON u.id=b.user_id WHERE u.auth0_sub=? AND (b.public_id=? OR CAST(b.id AS TEXT)=?)", (auth0_sub,connection_id,connection_id)).fetchone()
            if not owner: raise BrokerStorageError("TradeLocker connection was not found.")
            seen: list[tuple[str,str]] = []
            for item in accounts:
                if not isinstance(item,dict) or item.get("accountId") is None or item.get("accNum") is None: continue
                aid, anum = str(item["accountId"]), str(item["accNum"]); seen.append((aid,anum))
                existing = db.execute("SELECT id FROM broker_accounts WHERE connection_id=? AND broker_account_id=? AND acc_num=?", (owner["id"],aid,anum)).fetchone()
                active = str(item.get("status", "active")).lower() not in {"inactive","disabled","closed","blocked"}
                if existing:
                    db.execute("""UPDATE broker_accounts SET account_name=?,currency=?,environment=?,is_demo=?,broker_active=?,available=1,
                        metadata_json=?,last_verified_at=?,unavailable_since=NULL WHERE id=?""",
                        (item.get("name"),item.get("currency"),owner["environment"],1 if owner["environment"]=="demo" else 0 if owner["environment"]=="live" else None,
                         active,json.dumps(item,default=str),now,existing["id"]))
                else:
                    alias=self._unique_alias(db,owner["user_id"],f"{owner['server']}-{owner['environment']}-{anum}")
                    db.execute("""INSERT INTO broker_accounts(public_id,user_id,connection_id,broker_account_id,acc_num,account_alias,
                        account_name,currency,environment,is_demo,broker_active,is_default_analysis,metadata_json,first_discovered_at,last_verified_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(_ref("acct"),owner["user_id"],owner["id"],aid,anum,alias,item.get("name"),item.get("currency"),owner["environment"],
                        1 if owner["environment"]=="demo" else 0 if owner["environment"]=="live" else None,active,0,json.dumps(item,default=str),now,now))
            db.execute("UPDATE broker_accounts SET available=0,unavailable_since=COALESCE(unavailable_since,?) WHERE connection_id=?",(now,owner["id"]))
            for aid,anum in seen:
                db.execute("UPDATE broker_accounts SET available=1,unavailable_since=NULL WHERE connection_id=? AND broker_account_id=? AND acc_num=?",(owner["id"],aid,anum))
            db.execute("UPDATE broker_connections SET last_discovery_at=?,updated_at=?,needs_discovery_refresh=0,discovery_version=1,status='active' WHERE id=?",(now,now,owner["id"]))
        return self.list_accounts(auth0_sub)

    def list_accounts(self, auth0_sub: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows=db.execute("""SELECT a.public_id,a.acc_num account_number,a.account_alias,a.nickname,a.account_name,a.currency,a.environment,a.is_demo,a.broker_active,
                a.locally_enabled,a.available,a.is_default_analysis,a.last_verified_at,a.unavailable_since,b.public_id connection_id,b.label connection_label,b.server,b.broker_name
                FROM broker_accounts a JOIN users u ON u.id=a.user_id JOIN broker_connections b ON b.id=a.connection_id
                WHERE u.auth0_sub=? ORDER BY a.is_default_analysis DESC,a.account_alias COLLATE NOCASE""",(auth0_sub,)).fetchall()
        profiles=self.list_profiles(auth0_sub)
        result=[]
        for r in rows:
            item={**dict(r),"account_id":r["public_id"],"broker_name":r["server"],
                  "platform_name":"TradeLocker","broker_active":bool(r["broker_active"]),
                  "locally_enabled":bool(r["locally_enabled"]),"available":bool(r["available"]),
                  "is_default_analysis":bool(r["is_default_analysis"])}
            item["profiles"]=[p for p in profiles if p["account_id"] == r["public_id"]]
            result.append(item)
        return result

    def list_connection_tree(self, auth0_sub: str) -> list[dict[str,Any]]:
        accounts=self.list_accounts(auth0_sub)
        result=[]
        for connection in self.list_connections(auth0_sub):
            result.append({**connection,"accounts":[a for a in accounts if a["connection_id"] == connection["public_id"]]})
        return result

    def get_account_record(self, auth0_sub: str, *, alias: str | None=None, account_ref: str | None=None, profile: str | None=None) -> sqlite3.Row | None:
        with self._connect() as db:
            params: list[Any]=[auth0_sub]; condition="a.is_default_analysis=1"
            join=""
            if alias is not None: condition="a.account_alias=? COLLATE NOCASE"; params.append(alias)
            elif account_ref is not None: condition="a.public_id=?"; params.append(account_ref)
            elif profile is not None:
                join=" JOIN execution_profiles p ON p.broker_account_id=a.id"; condition="(p.public_id=? OR p.name=? COLLATE NOCASE)"; params.extend([profile,profile])
            profile_columns = "p.public_id profile_ref,p.enabled profile_enabled,p.execution_mode profile_execution_mode" if profile is not None else "NULL profile_ref,1 profile_enabled,NULL profile_execution_mode"
            return db.execute(f"""SELECT a.*,b.public_id connection_ref,b.base_url,b.username,b.password_encrypted,b.encryption_key_version,b.server,b.status connection_status,
                b.broker_name,b.label connection_label,{profile_columns}
                FROM broker_accounts a JOIN users u ON u.id=a.user_id JOIN broker_connections b ON b.id=a.connection_id {join}
                WHERE u.auth0_sub=? AND {condition} LIMIT 1""",params).fetchone()

    def rename_account(self, auth0_sub: str, account_ref: str, alias: str) -> bool:
        nickname=alias.strip();alias=self._slug(nickname)
        with self._connect() as db:
            try: cur=db.execute("""UPDATE broker_accounts SET account_alias=?,nickname=? WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)""",(alias,nickname,account_ref,auth0_sub))
            except sqlite3.IntegrityError: raise BrokerStorageError("That account alias is already in use.") from None
            return cur.rowcount==1

    def set_account_nickname(self, auth0_sub: str, account_ref: str, nickname: str | None) -> bool:
        value = nickname.strip() if nickname else None
        with self._connect() as db:
            return db.execute("""UPDATE broker_accounts SET nickname=? WHERE public_id=?
                AND user_id=(SELECT id FROM users WHERE auth0_sub=?)""",
                (value, account_ref, auth0_sub)).rowcount == 1

    def set_default_account(self, auth0_sub: str, account_ref: str) -> bool:
        with self._connect() as db:
            row=db.execute("SELECT a.id,a.connection_id,a.broker_account_id,a.acc_num,a.user_id FROM broker_accounts a JOIN users u ON u.id=a.user_id WHERE u.auth0_sub=? AND a.public_id=?",(auth0_sub,account_ref)).fetchone()
            if not row:return False
            db.execute("UPDATE broker_accounts SET is_default_analysis=0 WHERE user_id=?",(row["user_id"],)); db.execute("UPDATE broker_accounts SET is_default_analysis=1 WHERE id=?",(row["id"],))
            db.execute("UPDATE broker_connections SET account_id=NULL,account_number=NULL WHERE user_id=?",(row["user_id"],)); db.execute("UPDATE broker_connections SET account_id=?,account_number=? WHERE id=?",(row["broker_account_id"],row["acc_num"],row["connection_id"]))
            return True

    def set_account_enabled(self, auth0_sub: str, account_ref: str, enabled: bool) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE broker_accounts SET locally_enabled=? WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)",(enabled,account_ref,auth0_sub)).rowcount==1

    def select_account(self, auth0_sub: str, account_id: str, account_number: str, connection_ref: str | None=None) -> bool:
        connection=self.get_connection(auth0_sub,connection_ref)
        if not connection:return False
        with self._connect() as db:
            row=db.execute("SELECT public_id FROM broker_accounts WHERE connection_id=? AND broker_account_id=? AND acc_num=?",(int(connection.connection_id),account_id,account_number)).fetchone()
            if not row:
                owner=db.execute("SELECT user_id FROM broker_connections WHERE id=?",(int(connection.connection_id),)).fetchone()
                if not owner:return False
                now=_now(); alias=self._unique_alias(db,owner["user_id"],f"{connection.server}-{connection.environment}-{account_number}")
                public_id=_ref("acct")
                db.execute("""INSERT INTO broker_accounts(public_id,user_id,connection_id,broker_account_id,acc_num,account_alias,
                    environment,is_demo,available,is_default_analysis,first_discovered_at,last_verified_at)
                    VALUES(?,?,?,?,?,?,?,?,1,0,?,?)""",(public_id,owner["user_id"],int(connection.connection_id),account_id,account_number,alias,
                    connection.environment,1 if connection.environment=="demo" else 0 if connection.environment=="live" else None,now,now))
                row={"public_id":public_id}
        return self.set_default_account(auth0_sub,row["public_id"]) if row else False

    def disable_connection(self, auth0_sub: str, connection_ref: str) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE broker_connections SET status='disabled',updated_at=? WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)",(_now(),connection_ref,auth0_sub)).rowcount==1

    def connection_needs_discovery(self, auth0_sub: str, connection_ref: str) -> bool:
        with self._connect() as db:
            row=db.execute("""SELECT b.needs_discovery_refresh FROM broker_connections b JOIN users u ON u.id=b.user_id
                WHERE u.auth0_sub=? AND b.public_id=?""",(auth0_sub,connection_ref)).fetchone()
        return bool(row and row["needs_discovery_refresh"])

    def mark_reauthentication_required(self, auth0_sub: str, connection_ref: str) -> None:
        with self._connect() as db:
            db.execute("""UPDATE broker_connections SET status='reauthentication_required',needs_discovery_refresh=0,updated_at=?
                WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)""",(_now(),connection_ref,auth0_sub))

    def delete_connection(self, auth0_sub: str) -> bool:
        connection=self.get_connection(auth0_sub)
        return self.disable_connection(auth0_sub,connection.connection_ref) if connection else False

    def remove_connection(self,auth0_sub:str,connection_ref:str)->bool:
        with self._connect() as db:
            try:
                return db.execute("DELETE FROM broker_connections WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)",
                                  (connection_ref,auth0_sub)).rowcount==1
            except sqlite3.IntegrityError:
                raise BrokerStorageError("Delete bound strategies before removing this trading connection.") from None

    def list_profiles(self, auth0_sub: str) -> list[dict[str,Any]]:
        with self._connect() as db:
            rows=db.execute("""SELECT p.public_id,p.name,p.execution_mode,p.enabled,p.risk_json,p.allowed_instruments_json,
                p.session_rules_json,p.news_filter_enabled,p.autonomous_armed,p.armed_at,p.armed_until,
                p.decision_provider,p.model_identifier,p.minimum_confidence,p.allowed_sessions_json,p.schedule_ref,
                p.autonomous_shadow_mode,p.cooldown_minutes_after_loss,p.profile_v2_json,
                a.account_alias,a.acc_num account_number,a.nickname,a.public_id account_id,a.environment account_environment,a.is_demo,
                b.server broker_name,
                a.available account_available,a.broker_active,a.locally_enabled,
                t.public_id strategy_template_id,t.name strategy_name,t.version strategy_version,t.config_json strategy_config_json
                FROM execution_profiles p JOIN users u ON u.id=p.user_id JOIN broker_accounts a ON a.id=p.broker_account_id
                JOIN broker_connections b ON b.id=a.connection_id
                JOIN strategy_templates t ON t.id=p.strategy_template_id WHERE u.auth0_sub=? ORDER BY p.name COLLATE NOCASE""",(auth0_sub,)).fetchall()
        results=[{**dict(r),"profile_id":r["public_id"],"enabled":bool(r["enabled"]),"news_filter_enabled":bool(r["news_filter_enabled"]),
            "autonomous_armed":bool(r["autonomous_armed"]),"autonomous_shadow_mode":bool(r["autonomous_shadow_mode"]),
            "risk":json.loads(r["risk_json"]),"allowed_instruments":json.loads(r["allowed_instruments_json"]),
            "session_rules":json.loads(r["session_rules_json"]),"allowed_sessions":json.loads(r["allowed_sessions_json"]),
            "strategy_config":json.loads(r["strategy_config_json"])} for r in rows]
        for item in results:
            if item.get("profile_v2_json"):
                item["profile_v2"] = ExecutionProfileV2.model_validate_json(item["profile_v2_json"]).model_dump(mode="json")
                item["migration_state"] = "native_v2"
            else:
                item["profile_v2"] = migrate_legacy_profile(item).model_dump(mode="json")
                item["migration_state"] = "legacy_projected"
        return results

    def get_profile(self, auth0_sub: str, profile_ref: str) -> dict[str, Any] | None:
        return next((item for item in self.list_profiles(auth0_sub) if item["public_id"] == profile_ref), None)

    def update_profile_v2(self, auth0_sub: str, profile_ref: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_profile(auth0_sub, profile_ref)
        if current is None:
            return None
        normalized = ExecutionProfileV2.model_validate(deep_merge(current["profile_v2"], patch))
        value = normalized.model_dump(mode="json")
        allocation=value["capital_allocation"]
        peers=[item for item in self.list_profiles(auth0_sub) if item["account_id"]==current["account_id"]
               and item["public_id"]!=profile_ref and item.get("enabled") and item.get("migration_state")=="native_v2"]
        if not allocation.get("allow_shared_capital"):
            exclusive=[allocation,*[item["profile_v2"]["capital_allocation"] for item in peers
                         if not item["profile_v2"]["capital_allocation"].get("allow_shared_capital")]]
            if sum(float(item.get("equity_percentage") or 0) for item in exclusive if item.get("mode")=="equity_percentage")>100:
                raise BrokerStorageError("Enabled profiles allocate more than 100% of account equity.")
            if any(item.get("mode")=="full_account" for item in exclusive) and len(exclusive)>1:
                raise BrokerStorageError("A full-account allocation cannot overlap another exclusive profile allocation.")
        risk = value["risk_policy"]
        # Keep legacy readers operational during the compatibility window.
        legacy_risk = {**current["risk"], "risk_per_trade_percent": risk["fixed_risk_pct"] if risk["mode"] == "fixed" else risk["base_risk_pct"],
            "daily_loss_limit_percent": risk["daily_loss_limit_pct"], "drawdown_cutoff_percent": risk["drawdown_cutoff_pct"],
            "maximum_open_positions": risk["maximum_open_positions"], "maximum_pending_orders": risk["maximum_pending_entry_orders"],
            "maximum_new_entries_per_day": risk["maximum_new_entries_per_day"],
            "minimum_reward_risk": value["exit_policy"]["take_profit"]["minimum_reward_to_risk"]}
        universe = value["market_universe"]
        legacy_instruments = universe["included_instrument_ids"] if universe["mode"] == "custom" else current["allowed_instruments"]
        with self._connect() as db:
            db.execute("""UPDATE execution_profiles SET profile_v2_json=?,risk_json=?,allowed_instruments_json=?,
                minimum_confidence=?,enabled=?,updated_at=? WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)""",
                (json.dumps(value), json.dumps(legacy_risk), json.dumps(legacy_instruments),
                 value["trading_policy"]["minimum_confidence"], value["enabled"], _now(), profile_ref, auth0_sub))
        return self.get_profile(auth0_sub, profile_ref)

    def validate_profile_capital(self,auth0_sub:str,profile_ref:str,account_equity:float)->None:
        from app.services.trading_policy import authorized_capital
        profile=self.get_profile(auth0_sub,profile_ref)
        if profile is None:raise BrokerStorageError("Profile not found.")
        allocations=[]
        for item in self.list_profiles(auth0_sub):
            if item["account_id"]!=profile["account_id"] or not item.get("enabled"):continue
            if item["public_id"]!=profile_ref and item.get("migration_state")!="native_v2":continue
            allocation=item["profile_v2"]["capital_allocation"]
            if allocation.get("allow_shared_capital"):continue
            allocations.append(authorized_capital(account_equity=account_equity,allocation=allocation))
        if sum(allocations)>account_equity+1e-9:
            raise BrokerStorageError("Enabled profiles exceed current account equity allocation.")

    def account_connection_context(self, auth0_sub: str, account_alias: str) -> dict[str, Any] | None:
        row = self.get_account_record(auth0_sub, alias=account_alias)
        if row is None:
            return None
        try:
            password = self._cipher().decrypt(row["password_encrypted"], row["encryption_key_version"])
        except (InvalidToken, SecretDecryptionError) as exc:
            raise BrokerStorageError("Stored broker credentials cannot be decrypted.") from exc
        return {"base_url": row["base_url"], "username": row["username"], "password": password,
            "server": row["server"], "account_id": str(row["broker_account_id"]), "account_number": str(row["acc_num"]),
            "account_alias": row["account_alias"], "environment": row["environment"], "account_ref": row["public_id"]}

    def arm_autonomous_profile(self, auth0_sub: str, profile_ref: str, *, armed_until: str,
                               decision_provider: str="no_trade", model_identifier: str|None=None,
                               minimum_confidence: float=0.70, allowed_sessions: list[str]|None=None,
                               schedule_ref: str|None=None, shadow_mode: bool=True) -> dict[str,Any]:
        if decision_provider not in {"openai","no_trade"}:
            raise BrokerStorageError("Unsupported autonomous decision provider.")
        if not 0.5 <= float(minimum_confidence) <= 1.0:
            raise BrokerStorageError("Minimum confidence must be between 0.5 and 1.0.")
        sessions=allowed_sessions or ["london","new_york","overlap"]
        if not sessions or any(item not in {"london","new_york","overlap"} for item in sessions):
            raise BrokerStorageError("The autonomous session allowlist is invalid.")
        try:
            until=datetime.fromisoformat(armed_until.replace("Z","+00:00"))
        except ValueError:
            raise BrokerStorageError("The arming expiry is invalid.") from None
        if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
        now=datetime.now(timezone.utc)
        if until <= now or until > now + timedelta(hours=settings.autonomous_max_arming_hours):
            raise BrokerStorageError(f"Arming must expire within {settings.autonomous_max_arming_hours} hours.")
        with self._connect() as db:
            target=db.execute("""SELECT p.id,a.environment,a.is_demo FROM execution_profiles p
                JOIN users u ON u.id=p.user_id JOIN broker_accounts a ON a.id=p.broker_account_id
                WHERE p.public_id=? AND u.auth0_sub=? AND p.enabled=1""",(profile_ref,auth0_sub)).fetchone()
            if not target: raise BrokerStorageError("Enabled execution profile was not found.")
            if target["environment"]!="demo" or target["is_demo"]!=1:
                raise BrokerStorageError("Autonomous execution requires a verified demo account.")
            db.execute("""UPDATE execution_profiles SET execution_mode='demo_autonomous',autonomous_armed=1,
                armed_at=?,armed_until=?,armed_by_user=?,decision_provider=?,model_identifier=?,minimum_confidence=?,
                allowed_sessions_json=?,schedule_ref=?,autonomous_shadow_mode=?,updated_at=? WHERE id=?""",
                (now.isoformat(),until.astimezone(timezone.utc).isoformat(),auth0_sub,decision_provider,model_identifier,
                 minimum_confidence,json.dumps(sessions),schedule_ref,shadow_mode,now.isoformat(),target["id"]))
        return next(item for item in self.list_profiles(auth0_sub) if item["public_id"]==profile_ref)

    def disarm_autonomous_profile(self, auth0_sub: str, profile_ref: str) -> bool:
        now=_now()
        with self._connect() as db:
            cur=db.execute("""UPDATE execution_profiles SET autonomous_armed=0,armed_until=NULL,armed_by_user=NULL,
                execution_mode=CASE WHEN execution_mode='demo_autonomous' THEN 'demo_manual' ELSE execution_mode END,
                updated_at=? WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)""",
                (now,profile_ref,auth0_sub))
            return cur.rowcount==1

    def create_profile(self, auth0_sub: str, *, name: str, account_ref: str, strategy_template_id: str="strategy_hourly_forex_v1",
                       execution_mode: str="read_only", risk: dict|None=None, allowed_instruments: list[str]|None=None,
                       session_rules: dict|None=None, news_filter_enabled: bool=True) -> dict[str,Any]:
        if execution_mode not in {"read_only","demo_manual","demo_autonomous","disabled"}: raise BrokerStorageError("Invalid execution mode.")
        policy={"risk_per_trade_percent":0.25,"daily_loss_limit_percent":3.0,"drawdown_cutoff_percent":10.0,
            "maximum_open_positions":1,"maximum_pending_orders":1,"maximum_new_entries_per_day":2,"minimum_reward_risk":1.5}
        policy.update(risk or {})
        if not 0 < float(policy["risk_per_trade_percent"]) <= 1.0: raise BrokerStorageError("Risk per trade must be between 0 and 1 percent.")
        if float(policy["daily_loss_limit_percent"]) > 3 or float(policy["drawdown_cutoff_percent"]) > 10:
            raise BrokerStorageError("Profile risk limits exceed the demo safety ceiling.")
        instruments=[str(pair).replace("/","").upper() for pair in (allowed_instruments or ["EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD"])]
        if not instruments or any(pair not in {"EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD"} for pair in instruments):
            raise BrokerStorageError("The profile instrument allowlist is invalid.")
        now=_now()
        with self._connect() as db:
            user=db.execute("SELECT id FROM users WHERE auth0_sub=?",(auth0_sub,)).fetchone()
            account=db.execute("SELECT id,environment,is_demo FROM broker_accounts WHERE public_id=? AND user_id=?",(account_ref,user["id"] if user else -1)).fetchone()
            template=db.execute("SELECT id FROM strategy_templates WHERE public_id=?",(strategy_template_id,)).fetchone()
            if not account or not template: raise BrokerStorageError("Account or strategy template was not found.")
            if execution_mode == "demo_autonomous": raise BrokerStorageError("Demo Autonomous is not implemented.")
            if execution_mode == "demo_manual" and (account["environment"] != "demo" or account["is_demo"] != 1):
                raise BrokerStorageError("Demo Manual requires a verified demo account.")
            try: db.execute("""INSERT INTO execution_profiles(public_id,user_id,broker_account_id,strategy_template_id,name,execution_mode,
                risk_json,allowed_instruments_json,session_rules_json,news_filter_enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_ref("profile"),user["id"],account["id"],template["id"],name,execution_mode,json.dumps(policy),json.dumps(instruments),json.dumps(session_rules or {}),news_filter_enabled,now,now))
            except sqlite3.IntegrityError: raise BrokerStorageError("That profile name is already in use.") from None
        return next(p for p in self.list_profiles(auth0_sub) if p["name"].lower()==name.lower())

    def update_profile(self, auth0_sub: str, profile_ref: str, *, name: str | None=None,
                       execution_mode: str | None=None, enabled: bool | None=None,
                       strategy_template_id: str | None=None, risk: dict[str,Any] | None=None,
                       allowed_instruments: list[str] | None=None,
                       session_rules: dict[str,Any] | None=None,
                       news_filter_enabled: bool | None=None,
                       decision_provider: str | None=None, model_identifier: str | None=None,
                       minimum_confidence: float | None=None) -> bool:
        if execution_mode is not None and execution_mode not in {"read_only","demo_manual","demo_autonomous","disabled"}:
            raise BrokerStorageError("Invalid execution mode.")
        assignments, values = ["updated_at=?"], [_now()]
        if decision_provider is not None:
            if decision_provider not in {"openai","no_trade"}:
                raise BrokerStorageError("Unsupported autonomous decision provider.")
            assignments.extend(["decision_provider=?","model_identifier=?"])
            values.extend([decision_provider, model_identifier.strip() if model_identifier and model_identifier.strip() else None])
        elif model_identifier is not None:
            assignments.append("model_identifier=?"); values.append(model_identifier.strip() or None)
        if minimum_confidence is not None:
            if not 0 <= float(minimum_confidence) <= 1:
                raise BrokerStorageError("Minimum confidence must be between 0 and 1.")
            assignments.append("minimum_confidence=?"); values.append(float(minimum_confidence))
        if name is not None: assignments.append("name=?"); values.append(name)
        if execution_mode is not None:
            assignments.append("execution_mode=?"); values.append(execution_mode)
            if execution_mode != "demo_autonomous":
                assignments.extend(["autonomous_armed=0","armed_until=NULL","armed_by_user=NULL"])
        if enabled is not None:
            assignments.append("enabled=?"); values.append(enabled)
            if not enabled:assignments.extend(["autonomous_armed=0","armed_until=NULL","armed_by_user=NULL"])
        if risk is not None:
            current=next((item for item in self.list_profiles(auth0_sub) if item["public_id"]==profile_ref),None)
            if current is None: return False
            policy={**current["risk"],**risk}
            if not 0 < float(policy.get("risk_per_trade_percent",0)) <= 1.0:
                raise BrokerStorageError("Risk per trade must be between 0 and 1 percent.")
            if not 0 < float(policy.get("daily_loss_limit_percent",0)) <= 3.0:
                raise BrokerStorageError("Daily loss limit must be between 0 and 3 percent.")
            if not 0 < float(policy.get("drawdown_cutoff_percent",0)) <= 10.0:
                raise BrokerStorageError("Drawdown cutoff must be between 0 and 10 percent.")
            for key in ("maximum_open_positions","maximum_pending_orders","maximum_new_entries_per_day"):
                if not 1 <= int(policy.get(key,0)) <= 100: raise BrokerStorageError(f"Invalid {key.replace('_',' ')}.")
            if float(policy.get("minimum_reward_risk",0)) < 1.5:
                raise BrokerStorageError("Minimum reward-to-risk cannot be below 1.5.")
            assignments.append("risk_json=?");values.append(json.dumps(policy))
        if allowed_instruments is not None:
            instruments=[str(pair).replace("/","").upper() for pair in allowed_instruments]
            if not instruments or any(pair not in {"EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD"} for pair in instruments):
                raise BrokerStorageError("The profile instrument allowlist is invalid.")
            assignments.append("allowed_instruments_json=?");values.append(json.dumps(instruments))
        if session_rules is not None:assignments.append("session_rules_json=?");values.append(json.dumps(session_rules))
        if news_filter_enabled is not None:assignments.append("news_filter_enabled=?");values.append(news_filter_enabled)
        if strategy_template_id is not None:
            with self._connect() as lookup:
                template=lookup.execute("SELECT id FROM strategy_templates WHERE public_id=?",(strategy_template_id,)).fetchone()
            if not template:raise BrokerStorageError("Strategy template was not found.")
            assignments.append("strategy_template_id=?");values.append(template["id"])
        values.extend([profile_ref,auth0_sub])
        with self._connect() as db:
            if execution_mode in {"demo_manual","demo_autonomous"}:
                target=db.execute("""SELECT a.environment,a.is_demo FROM execution_profiles p JOIN broker_accounts a ON a.id=p.broker_account_id
                    JOIN users u ON u.id=p.user_id WHERE p.public_id=? AND u.auth0_sub=?""",(profile_ref,auth0_sub)).fetchone()
                if not target or target["environment"] != "demo" or target["is_demo"] != 1:
                    raise BrokerStorageError("Demo execution requires a verified demo account.")
                if execution_mode == "demo_autonomous": raise BrokerStorageError("Demo Autonomous is not implemented.")
            try: cur=db.execute(f"UPDATE execution_profiles SET {','.join(assignments)} WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)",values)
            except sqlite3.IntegrityError: raise BrokerStorageError("That profile name is already in use.") from None
            return cur.rowcount==1

    def delete_profile(self, auth0_sub: str, profile_ref: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM execution_profiles WHERE public_id=? AND user_id=(SELECT id FROM users WHERE auth0_sub=?)",(profile_ref,auth0_sub)).rowcount==1

    def status(self, auth0_sub: str) -> dict:
        account=self.get_account_record(auth0_sub)
        if not self.list_connections(auth0_sub): return {"status":"not_connected","connected":False,"selected_account":None}
        if not account:return {"status":"connected_no_account","connected":True,"selected_account":None}
        return {"status":"ready","connected":True,"selected_account":{"server":account["server"],"environment":account["environment"],"account_id":account["broker_account_id"],"account_number":account["acc_num"],"account_alias":account["account_alias"]}}
