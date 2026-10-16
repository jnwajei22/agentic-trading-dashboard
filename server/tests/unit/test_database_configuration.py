import pytest
from app.config.settings import settings
from app.storage.database import DatabaseUnavailable, postgres_dsn, postgres_sql, storage_path


def test_production_never_creates_sqlite(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'app_env','production')
    monkeypatch.setattr(settings,'storage_backend','sqlite')
    path=tmp_path/'no-disk'/'app.db'
    with pytest.raises(DatabaseUnavailable):storage_path(path)
    assert not path.parent.exists()


def test_database_targets_and_tls_fail_closed(monkeypatch):
    monkeypatch.setattr(settings,'app_env','production')
    for value in [None,'not-a-dsn','postgresql://user:password@db.neon.tech/postgres','postgresql://user:password@db.example.com/postgres','postgresql://user:password@db.example.supabase.co/postgres?sslmode=disable']:
        monkeypatch.setattr(settings,'supabase_db_url',value)
        with pytest.raises(DatabaseUnavailable):postgres_dsn()
    monkeypatch.setattr(settings,'supabase_db_url','postgresql://user:password@db.example.supabase.co/postgres?sslmode=verify-full')
    assert 'verify-full' in postgres_dsn()


def test_query_translation_keeps_literals_and_bound_parameters_separate():
    assert postgres_sql("SELECT '?' AS literal WHERE account_alias=? COLLATE NOCASE") == "SELECT '?' AS literal WHERE lower(account_alias)=lower(%s)"
    assert postgres_sql("SELECT name FROM users WHERE email LIKE '%example%' AND id=?") == "SELECT name FROM users WHERE email LIKE '%%example%%' AND id=%s"
    assert postgres_sql('INSERT OR IGNORE INTO test(id) VALUES(?)').endswith('VALUES(%s) ON CONFLICT DO NOTHING')
