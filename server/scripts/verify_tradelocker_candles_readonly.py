"""Read-only verification for one immutable profile/account-bound candle feed."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.config.settings import settings
from app.storage.brokers import BrokerRepository


async def verify(alias: str, symbol: str) -> dict:
    with sqlite3.connect(settings.sqlite_path) as db:
        row = db.execute(
            """SELECT u.auth0_sub FROM broker_accounts a
               JOIN users u ON u.id=a.user_id WHERE a.account_alias=?""",
            (alias,),
        ).fetchone()
    if row is None:
        return {"status": "blocked", "error": "account_alias_not_found", "alias": alias}
    user_sub = str(row[0])
    repository = BrokerRepository()
    account = repository.get_account_record(user_sub, alias=alias)
    if account is None or account["environment"] != "demo" or account["is_demo"] != 1:
        return {"status": "blocked", "error": "verified_demo_account_required", "alias": alias}
    connection = repository.get_connection(user_sub, str(account["connection_ref"]))
    if connection is None:
        return {"status": "blocked", "error": "connection_unavailable", "alias": alias}
    client = TradeLockerClient(
        base_url=connection.base_url, username=connection.username, password=connection.password,
        server=connection.server, account_id=str(account["broker_account_id"]),
        account_number=str(account["acc_num"]),
        cache_user_id=user_sub, cache_connection_id=str(account["connection_ref"]),
        cache_account_record_id=str(account["public_id"]),
    )
    results = {}
    try:
        for timeframe, requested in (("1d", 190), ("4h", 250), ("1h", 200), ("15m", 200)):
            try:
                result = await client.get_candles(
                    symbol, timeframe, requested, minimum_usable=50
                )
                canonical = result.canonical_dict()
                metadata = canonical["metadata"]
                results[timeframe] = {
                    "status": canonical["status"],
                    "provider_timeframe": canonical["provider_timeframe"],
                    "http_status": result.http_status,
                    "raw_count": metadata["raw_count"],
                    "normalized_count": metadata["normalized_count"],
                    "usable_count": metadata["usable_count"],
                    "latest_complete_timestamp": metadata["latest_complete_timestamp"],
                    "unexpected_gap_count": metadata["unexpected_gap_count"],
                    "unexpected_gap_ranges": metadata["unexpected_gap_ranges"],
                    "accepted_market_closure_gaps": metadata["accepted_market_closure_gaps"],
                    "source": canonical["source"],
                    "strategy_satisfied": metadata["is_sufficient"],
                    "blocking_reasons": canonical["blocking_reasons"],
                    "warnings": canonical["warnings"],
                }
            except TradeLockerError as exc:
                results[timeframe] = {
                    "status": "blocked", "provider_timeframe": None,
                    "http_status": exc.status_code, "error": exc.code,
                    "blocking_reasons": ["provider_request_failed"],
                }
    finally:
        await client.aclose()
    return {"status": "ok", "account_alias": alias, "symbol": symbol, "timeframes": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="herofx-demo-1")
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.alias, args.symbol)), indent=2))


if __name__ == "__main__":
    main()
