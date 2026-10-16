import sqlite3

from app.storage.brokers import BrokerRepository


def test_existing_live_connection_environment_is_migrated(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, auth0_sub TEXT NOT NULL UNIQUE, email TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE broker_connections (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE,
                provider TEXT NOT NULL, base_url TEXT NOT NULL, username TEXT NOT NULL,
                password_encrypted BLOB NOT NULL, server TEXT NOT NULL,
                account_id TEXT, account_number TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO users VALUES (1, 'auth0|legacy', NULL, 'now', 'now');
            INSERT INTO broker_connections VALUES (
                1, 1, 'tradelocker', 'https://live.tradelocker.com/backend-api',
                'legacy', X'00', 'HEROFX', '1', '2', 'now', 'now'
            );
            """
        )
    BrokerRepository(database, "test-secret")
    with sqlite3.connect(database) as connection:
        environment = connection.execute(
            "SELECT environment FROM broker_connections WHERE id = 1"
        ).fetchone()[0]
        account = connection.execute(
            "SELECT account_alias, is_default_analysis FROM broker_accounts"
        ).fetchone()
        profile = connection.execute(
            "SELECT execution_mode FROM execution_profiles"
        ).fetchone()
    assert environment == "live"
    assert account[0] and account[1] == 1
    assert profile[0] == "read_only"


def test_explicit_environment_is_stored_with_connection(tmp_path):
    repository = BrokerRepository(tmp_path / "app.db", "test-secret")
    repository.save_connection(
        "auth0|user", base_url="https://custom.example/backend-api",
        username="user", password="secret", server="HEROFX", environment="live",
    )
    stored = repository.get_connection("auth0|user")
    assert stored is not None
    assert stored.environment == "live"
    assert stored.connection_id
