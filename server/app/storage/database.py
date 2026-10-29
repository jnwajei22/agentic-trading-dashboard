"""Database boundary for the desk repositories.

SQLite is local-development only. Production uses an explicitly configured
Supabase Postgres connection and a preinstalled private schema, never local disk.
No data is copied between databases.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg2
from psycopg2.extras import DictCursor
from app.config.settings import settings

IntegrityError = (sqlite3.IntegrityError, psycopg2.IntegrityError)
SCHEMA_VERSION = 1
# Transaction-scoped lock preserves the old BEGIN IMMEDIATE critical sections.
LEASE_LOCK = 761243089


class DatabaseUnavailable(RuntimeError):
    pass


def using_postgres():
    return settings.storage_backend == 'postgres'


def storage_path(path=None):
    value = Path(path or settings.sqlite_path)
    if not using_postgres():
        if settings.app_env == 'production':
            raise DatabaseUnavailable('Production requires Supabase Postgres storage.')
        value.parent.mkdir(parents=True, exist_ok=True)
    return value


def postgres_dsn():
    value = settings.supabase_db_url
    if not value:
        raise DatabaseUnavailable('Supabase database connection is not configured.')
    parsed = urlparse(value)
    if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname:
        raise DatabaseUnavailable('Supabase database connection is invalid.')
    if 'neon.tech' in parsed.hostname.lower():
        raise DatabaseUnavailable('Neon is not an authorized database target.')
    if settings.app_env == 'production':
        ssl = parse_qs(parsed.query).get('sslmode', ['require'])[0]
        if ssl not in {'require', 'verify-ca', 'verify-full'}:
            raise DatabaseUnavailable('Production database connections require TLS.')
        if not parsed.hostname.endswith(('.supabase.co', '.supabase.com')):
            raise DatabaseUnavailable('Production database must be a Supabase endpoint.')
    return value


def postgres_sql(sql):
    """Translate the bounded repository SQL dialect, keeping parameters separate."""
    sql = re.sub(r'([\w.]+)\s*=\s*\?\s+COLLATE NOCASE', r'lower(\1)=lower(?)', sql, flags=re.I)
    sql = re.sub(r'([\w.]+)\s+COLLATE NOCASE', r'lower(\1)', sql, flags=re.I)
    ignored = bool(re.match(r'\s*INSERT OR IGNORE\b', sql, re.I))
    if ignored:
        sql = re.sub(r'INSERT OR IGNORE', 'INSERT', sql, count=1, flags=re.I).rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    sql = sql.replace('MAX(0,COUNT(*)-?)', 'GREATEST(0,COUNT(*)-?)')
    sql = sql.replace('MAX(cooldown_until,excluded.cooldown_until)', 'GREATEST(tradelocker_request_limits.cooldown_until,excluded.cooldown_until)')
    sql = re.sub(r'\browid\b', 'ctid', sql)
    # Psycopg's percent placeholders must not interpret literal LIKE wildcards.
    sql = sql.replace('%', '%%')
    tokens = r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/)|\?|(?<!:):([A-Za-z_][A-Za-z0-9_]*)"
    return re.sub(tokens, lambda match: match.group(1) or ('%(' + match.group(2) + ')s' if match.group(2) else '%s'), sql)


class Result:
    def __init__(self, cursor=None, rows=None, lastrowid=None):
        self.cursor = cursor
        self.rows = iter(rows) if rows is not None else None
        self.rowcount = cursor.rowcount if cursor is not None else 0
        self.lastrowid = lastrowid
    @staticmethod
    def convert(row):
        if row is not None:
            for index, value in enumerate(row):
                if isinstance(value, memoryview): row[index] = bytes(value)
                elif isinstance(value, Decimal): row[index] = float(value)
        return row
    def fetchone(self):
        return self.convert(next(self.rows, None) if self.rows is not None else self.cursor.fetchone())
    def fetchall(self):
        return [self.convert(row) for row in (list(self.rows) if self.rows is not None else self.cursor.fetchall())]
    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self):
        try:
            self.raw = psycopg2.connect(postgres_dsn(), connect_timeout=8,
                sslmode=parse_qs(urlparse(settings.supabase_db_url).query).get('sslmode', ['require' if settings.app_env == 'production' else 'prefer'])[0])
            self._configure()
        except DatabaseUnavailable:
            raise
        except psycopg2.Error:
            if hasattr(self, 'raw'): self.raw.close()
            raise DatabaseUnavailable('Supabase database is unavailable.') from None
    def _configure(self):
        cursor = self.raw.cursor(cursor_factory=DictCursor)
        cursor.execute('SET LOCAL ROLE desk_runtime')
        cursor.execute('SET LOCAL search_path TO desk, pg_catalog')
        cursor.execute("SET LOCAL statement_timeout = '15s'")
        cursor.execute("SET LOCAL lock_timeout = '8s'")
    def __enter__(self):
        return self
    def __exit__(self, kind, value, traceback):
        try:
            self.raw.rollback() if kind else self.raw.commit()
        finally:
            self.raw.close()
    def close(self):
        self.raw.close()
    def execute(self, sql, params=()):
        command = sql.strip().upper()
        cursor = self.raw.cursor(cursor_factory=DictCursor)
        if command.startswith('PRAGMA FOREIGN_KEYS'):
            return Result(rows=[])
        if command == 'BEGIN IMMEDIATE':
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', (LEASE_LOCK,))
            return Result(cursor)
        if command == 'COMMIT':
            self.raw.commit(); self._configure(); return Result(rows=[])
        if command.startswith(('CREATE ', 'ALTER ', 'DROP ')):
            raise DatabaseUnavailable('Schema installation is an explicit deployment step.')
        # Metadata queries remain for safe legacy-control projection, not migrations.
        if 'sqlite_master' in sql:
            sql = "SELECT table_name AS name FROM information_schema.tables WHERE table_schema='desk'"
            params = ()
        elif command.startswith('PRAGMA TABLE_INFO('):
            name = re.fullmatch(r'PRAGMA table_info\((\w+)\)', sql.strip(), re.I)
            if not name: raise ValueError('Unsupported metadata query')
            sql = "SELECT column_name AS name FROM information_schema.columns WHERE table_schema='desk' AND table_name=?"
            params = (name.group(1),)
        sql = postgres_sql(sql)
        returning = bool(re.match(r'\s*INSERT INTO broker_connections\b', sql, re.I))
        if returning: sql += ' RETURNING id'
        if isinstance(params, Mapping):
            params = {key: int(value) if isinstance(value, bool) else value for key, value in params.items()}
        else:
            params = tuple(int(value) if isinstance(value, bool) else value for value in params)
        # Savepoints preserve repository handling of duplicate claims within a transaction.
        cursor.execute('SAVEPOINT desk_statement')
        try:
            cursor.execute(sql, params)
        except psycopg2.Error:
            cursor.execute('ROLLBACK TO SAVEPOINT desk_statement')
            cursor.execute('RELEASE SAVEPOINT desk_statement')
            raise
        lastrowid = cursor.fetchone()[0] if returning else None
        # A separate cursor must release the savepoint without replacing SELECT rows.
        with self.raw.cursor() as control: control.execute('RELEASE SAVEPOINT desk_statement')
        return Result(cursor, lastrowid=lastrowid)
    def executemany(self, sql, values):
        for params in values: self.execute(sql, params)


def connect(path=None, *, timeout=30, isolation_level=''):
    if using_postgres():
        return PostgresConnection()
    db = sqlite3.connect(storage_path(path), timeout=timeout, isolation_level=isolation_level)
    return db


def check_database():
    """Read-only readiness check; never initializes or changes a remote schema."""
    if not using_postgres():
        storage_path()
        return {'backend': 'sqlite', 'ready': True}
    try:
        with connect() as db:
            row = db.execute('SELECT version FROM schema_version WHERE id=1').fetchone()
            if row is None or row['version'] != SCHEMA_VERSION:
                raise DatabaseUnavailable('Database schema version is not supported.')
        return {'backend': 'postgres', 'ready': True}
    except DatabaseUnavailable:
        raise
    except psycopg2.Error:
        raise DatabaseUnavailable('Database schema is not installed or accessible.') from None
