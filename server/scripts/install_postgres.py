"""Install the desk's fresh schema into the explicitly configured Supabase DB.

Refuses any existing desk schema. Does not read/copy SQLite data or touch Neon.
Run from server: python -m scripts.install_postgres
"""
from pathlib import Path
import psycopg2
from app.config.settings import settings
from app.storage.database import postgres_dsn, check_database


def main():
    if settings.storage_backend != 'postgres':
        raise SystemExit('Set STORAGE_BACKEND=postgres and SUPABASE_DB_URL first.')
    dsn = postgres_dsn()
    schema = Path(__file__).resolve().parents[2]/'supabase/schema.sql'
    try:
        with psycopg2.connect(dsn, connect_timeout=8) as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT to_regnamespace('desk')")
                if cursor.fetchone()[0] is not None:
                    raise SystemExit('A desk schema already exists; installation refused without modifying it.')
                cursor.execute(schema.read_text())
    except psycopg2.Error:
        raise SystemExit('Schema installation failed; transaction rolled back. Database credentials were not logged.') from None
    print(check_database())


if __name__ == '__main__': main()
