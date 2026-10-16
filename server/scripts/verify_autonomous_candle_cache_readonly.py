"""Run two broker-read-only snapshots and report only compact candle diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3

from app.config.settings import settings
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError


def compact(snapshot: dict) -> dict:
    pairs = {}
    for symbol, market in snapshot.get("market", {}).get("pairs", {}).items():
        pairs[symbol] = {
            timeframe: {
                "status": result.get("status"),
                "source": result.get("source"),
                **{key: (result.get("metadata") or {}).get(key) for key in (
                    "cache_hit", "cache_fresh", "cache_age_seconds", "upstream_request_made",
                    "attempts", "retry_count", "usable_count", "newest_completed_timestamp",
                    "is_sufficient", "cooldown_until",
                )},
                "blocking_reasons": result.get("blocking_reasons", []),
                "warnings": result.get("warnings", []),
            }
            for timeframe, result in market.get("timeframes", {}).items()
        }
    return {"status": snapshot.get("status"), "snapshot_id": snapshot.get("snapshot_id"),
            "candle_requests": snapshot.get("candle_requests"), "pairs": pairs}


async def verify(profile_ref: str, symbol: str) -> dict:
    with sqlite3.connect(settings.sqlite_path) as db:
        row = db.execute("""SELECT u.auth0_sub FROM execution_profiles p
            JOIN users u ON u.id=p.user_id WHERE p.public_id=?""", (profile_ref,)).fetchone()
    if row is None:
        return {"status": "blocked", "error": "profile_not_found"}
    service = AutonomousDemoService()
    runs = []
    for _ in range(2):
        try:
            runs.append(compact(await service.snapshot(
                str(row[0]), profile_ref, symbol, autonomous=True
            )))
        except AutonomousExecutionError as exc:
            runs.append(exc.as_dict())
    return {"status": "ok", "profile_ref": profile_ref, "runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--symbol", default="GBPUSD")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.profile, args.symbol)), indent=2))


if __name__ == "__main__":
    main()
