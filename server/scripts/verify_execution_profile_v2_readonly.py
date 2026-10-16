"""Read-only V2 verification. This module contains no broker mutation call."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from app.brokers.tradelocker.client import TradeLockerClient
from app.brokers.tradelocker.mapping import map_configured_rows
from app.services.trading_policy import classify_orders, count_open_positions, market_is_open, normalize_instrument, resolve_universe
from app.storage.brokers import BrokerRepository


async def verify(user_sub: str, account_alias: str) -> dict:
    repo=BrokerRepository();context=repo.account_connection_context(user_sub,account_alias)
    if context is None: raise RuntimeError("Owned account alias was not found.")
    profiles=[item for item in repo.list_profiles(user_sub) if item["account_alias"].casefold()==account_alias.casefold()]
    async with TradeLockerClient(base_url=context["base_url"],username=context["username"],password=context["password"],
        server=context["server"],account_id=context["account_id"],account_number=context["account_number"]) as client:
        # Deliberately restricted to GET-backed client methods.
        config,symbols_payload,positions_payload,orders_payload=await asyncio.gather(
            client.get_config(),client.get_symbols(),client.get_open_positions(),client.get_orders())
    instruments=[item for row in TradeLockerClient._instrument_rows(symbols_payload) if (item:=normalize_instrument(row)) is not None]
    positions=map_configured_rows(config_response=config,data_response=positions_payload,config_key="positionsConfig",data_key="positions")
    orders=map_configured_rows(config_response=config,data_response=orders_payload,config_key="ordersConfig",data_key="orders")
    classification=classify_orders(positions,orders);latest=profiles[0] if len(profiles)==1 else None
    maximum=(latest or {}).get("profile_v2",{}).get("risk_policy",{}).get("maximum_open_positions")
    open_positions=count_open_positions(positions);pending=classification["counts"]["pending_entry"]
    selected=[]
    if latest:
        universe=latest["profile_v2"]["market_universe"];selected=resolve_universe(instruments,universe)
        if universe["mode"]=="custom" and not selected:
            wanted={str(value).replace("/","").upper() for value in universe["included_instrument_ids"]}
            selected=[item for item in instruments if item["broker_symbol"].replace("/","").upper() in wanted]
    selected_open=[item for item in selected if market_is_open(item,datetime.now(timezone.utc))]
    return {"account_alias":account_alias,"instrument_count":len(instruments),
        "market_groups":sorted({item["market_group"] for item in instruments}),
        "tradable_now":sum(market_is_open(item,datetime.now(timezone.utc)) for item in instruments),
        "open_positions":open_positions,"pending_entry_orders":pending,
        "protective_stop_orders":classification["counts"]["protective_stop_loss"],
        "protective_take_profit_orders":classification["counts"]["protective_take_profit"],
        "maximum_open_positions":maximum,"can_open_position":bool(maximum is not None and open_positions < maximum and pending < (latest or {}).get("profile_v2",{}).get("risk_policy",{}).get("maximum_pending_entry_orders",0)),
        "selected_instruments":len(selected),"selected_markets_open":len(selected_open),
        "market_check_outcome":"ELIGIBLE" if selected_open else "MARKET_CLOSED",
        "profile_count":len(profiles),"broker_methods_called":["get_config","get_symbols","get_open_positions","get_orders"],
        "broker_write_methods_called":[]}


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--user-sub",required=True);parser.add_argument("--account-alias",default="herofx-demo-1")
    args=parser.parse_args();print(json.dumps(asyncio.run(verify(args.user_sub,args.account_alias)),indent=2,sort_keys=True))


if __name__=="__main__":main()
