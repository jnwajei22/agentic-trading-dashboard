from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import settings


CONFIRMATION = "DISABLE-DEMO-KILL-SWITCH"


def disable(database: Path, confirmation: str, actor: str) -> None:
    if confirmation != CONFIRMATION:
        raise ValueError("Exact local confirmation phrase is required")
    if not actor.strip():
        raise ValueError("A local operator identifier is required")
    if not database.is_file():
        raise FileNotFoundError("Configured SQLite database does not exist")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operational_controls'"
        ).fetchone()
        if not table:
            raise RuntimeError("Operational-control migration is not current")
        connection.execute("""INSERT INTO operational_controls(key,value,updated_at,updated_by)
            VALUES('kill_switch','disabled',?,?) ON CONFLICT(key) DO UPDATE SET
            value='disabled',updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (now, f"local:{actor.strip()}"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local-only recovery command for the durable DEMO execution kill switch"
    )
    parser.add_argument("--confirm", required=True, help=f"Must equal {CONFIRMATION}")
    parser.add_argument("--actor", required=True, help="Non-secret local operator identifier")
    args = parser.parse_args()
    disable(Path(settings.sqlite_path), args.confirm, args.actor)
    print("Demo execution kill switch disabled locally; rerun readiness before any execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
