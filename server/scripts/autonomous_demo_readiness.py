from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.brokers.tradelocker.client import TradeLockerClient
from app.brokers.tradelocker.mapping import map_configured_array, map_configured_rows
from app.config.settings import settings
from app.services.autonomous.execution import parse_instrument_metadata
from app.services.providers.finnhub import FinnhubClient
from app.services.providers.fred import FredClient


@dataclass
class Check:
    name:str
    passed:bool
    detail:str


class Gate:
    def __init__(self)->None:self.checks:list[Check]=[]
    def add(self,name:str,passed:bool,success:str,failure:str)->None:self.checks.append(Check(name,passed,success if passed else failure))
    def fail(self,name:str,detail:str)->None:self.checks.append(Check(name,False,detail))
    def print(self)->int:
        for item in self.checks:print(f"{'PASS' if item.passed else 'FAIL'} {item.name}: {item.detail}")
        passed=all(item.passed for item in self.checks)
        print(f"RESULT: {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1


def _connect_readonly(path:Path)->sqlite3.Connection:
    db=sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True);db.row_factory=sqlite3.Row
    db.execute("PRAGMA query_only=ON");return db


def _value(payload:Any,names:tuple[str,...])->Any:
    lowered={name.lower() for name in names}
    if isinstance(payload,dict):
        for key,value in payload.items():
            if key.lower() in lowered and value is not None:return value
        for value in payload.values():
            found=_value(value,names)
            if found is not None:return found
    if isinstance(payload,list):
        for value in payload:
            found=_value(value,names)
            if found is not None:return found
    return None


def _decrypt(ciphertext:bytes)->str:
    if not settings.broker_secret_key:raise RuntimeError("BROKER_SECRET_KEY is not configured")
    key=base64.urlsafe_b64encode(hashlib.sha256(settings.broker_secret_key.encode()).digest())
    return Fernet(key).decrypt(ciphertext).decode()


def _profile(db:sqlite3.Connection,selector:str,user_sub:str|None)->sqlite3.Row:
    params:list[Any]=[selector,selector]
    user_clause=""
    if user_sub:user_clause=" AND u.auth0_sub=?";params.append(user_sub)
    rows=db.execute(f"""SELECT p.*,u.auth0_sub,a.public_id account_ref,a.account_alias,a.environment account_environment,
        a.is_demo,a.available,a.broker_active,a.locally_enabled,a.broker_account_id,a.acc_num,a.currency,
        b.public_id connection_ref,b.base_url,b.username,b.password_encrypted,b.server,b.environment connection_environment,
        b.status connection_status,t.public_id strategy_ref,t.name strategy_name,t.version strategy_version,t.config_json strategy_config_json
        FROM execution_profiles p JOIN users u ON u.id=p.user_id JOIN broker_accounts a ON a.id=p.broker_account_id
        JOIN broker_connections b ON b.id=a.connection_id JOIN strategy_templates t ON t.id=p.strategy_template_id
        WHERE (p.public_id=? OR p.name=? COLLATE NOCASE){user_clause}""",params).fetchall()
    if len(rows)!=1:raise RuntimeError("Profile selector was not found or was ambiguous; pass --user when needed")
    return rows[0]


async def _network_checks(gate:Gate,row:sqlite3.Row,risk:dict[str,Any],strategy:dict[str,Any])->None:
    client=TradeLockerClient(base_url=row["base_url"],username=row["username"],password=_decrypt(row["password_encrypted"]),
        server=row["server"],account_id=row["broker_account_id"],account_number=row["acc_num"])
    try:
        accounts=await client.get_accounts();items=accounts.get("accounts",[]) if isinstance(accounts,dict) else []
        matched=next((item for item in items if isinstance(item,dict) and str(item.get("accountId"))==row["broker_account_id"] and str(item.get("accNum"))==row["acc_num"]),None)
        gate.add("broker_account_verification",matched is not None,"profile-bound demo account was re-verified","profile-bound account was not returned by TradeLocker")
        config=await client.get_config();state=await client.get_account_state_payload()
        values=map_configured_array(config_response=config,data_response=state,config_key="accountDetailsConfig",data_key="accountDetailsData")
        balance=float(values.get("balance",0));equity=float(values.get("projectedBalance",0));funds=float(values.get("availableFunds",0))
        gate.add("positive_funds",all(math.isfinite(item) and item>0 for item in (balance,equity,funds)),
            "balance, equity, and available funds are positive","balance, equity, or available funds is unavailable/non-positive")
        positions=map_configured_rows(config_response=config,data_response=await client.get_open_positions(),config_key="positionsConfig",data_key="positions")
        orders=map_configured_rows(config_response=config,data_response=await client.get_orders(),config_key="ordersConfig",data_key="orders")
        gate.add("position_order_conflicts",len(positions)<int(risk.get("maximum_open_positions",1)) and len(orders)<int(risk.get("maximum_pending_orders",1)),
            "open-position and pending-order capacity is available","an open position or pending-order limit is already reached")
        pairs=json.loads(row["allowed_instruments_json"]);symbol=pairs[0] if pairs else ""
        quote=await client.get_quote(symbol);bid=float(_value(quote,("bid","bidPrice","bp","b")));ask=float(_value(quote,("ask","askPrice","ap","a")))
        candles=await client.get_candles(symbol,"15m",60);instrument=parse_instrument_metadata(await client.get_instrument_details(symbol),symbol)
        entry=(bid+ask)/2;minimum_margin=(instrument.min_lots*instrument.contract_size*entry/instrument.leverage) if instrument.leverage else math.inf
        gate.add("quote_candles_metadata",ask>=bid>0 and len(candles.candles)>=50 and candles.complete and bool(instrument.instrument_id) and bool(instrument.route_id),
            "quote, complete candles, and instrument metadata are readable","quote, candles, or instrument metadata is incomplete")
        gate.add("minimum_sizing",instrument.min_lots>0 and instrument.lot_step>0 and minimum_margin<=funds,
            "broker minimum size fits available funds","broker minimum size or margin cannot be validated")
        total_loss=float(values.get("todayNet",0))+float(values.get("openNetPnL",0))
        gate.add("daily_loss",total_loss>-(balance*float(risk.get("daily_loss_limit_percent",3))/100),
            "daily loss cutoff has not been reached","daily loss cutoff has been reached")
        high=float(risk.get("_equity_high_watermark") or equity);drawdown=max(0,(high-equity)/high*100) if high>0 else 100
        gate.add("drawdown",drawdown<float(risk.get("drawdown_cutoff_percent",10)),
            "drawdown cutoff has not been reached","drawdown cutoff has been reached")
    except Exception as exc:
        gate.fail("tradelocker_reads",f"sanitized broker read failure ({type(exc).__name__})")
    finally:await client.aclose()
    finn=FinnhubClient()
    try:
        await finn.economic_calendar(date.today(),date.today());gate.add("finnhub",True,"provider read succeeded","provider read failed")
    except Exception as exc:gate.fail("finnhub",f"provider unavailable ({type(exc).__name__})")
    finally:await finn.aclose()
    fred_required=bool(strategy.get("required_macro_series"));fred=FredClient()
    try:
        await fred.release_dates(date.today(),date.today(),5);gate.add("fred",True,"required macro provider read succeeded","required macro provider read failed")
    except Exception as exc:
        gate.add("fred",not fred_required,"optional macro provider is unavailable",f"required macro provider unavailable ({type(exc).__name__})")
    finally:await fred.aclose()
    if row["decision_provider"]=="openai":
        if not settings.openai_api_key:gate.fail("openai","OpenAI provider key is not configured")
        else:
            api=None
            try:
                from openai import AsyncOpenAI
                api=AsyncOpenAI(api_key=settings.openai_api_key,timeout=settings.autonomous_decision_timeout_seconds,max_retries=0)
                await api.models.retrieve(row["model_identifier"] or settings.autonomous_decision_model)
                gate.add("openai",True,"configured decision model is reachable","decision model is unreachable")
            except Exception as exc:gate.fail("openai",f"decision provider unavailable ({type(exc).__name__})")
            finally:
                if api:await api.close()
    else:gate.fail("openai","profile decision provider is fail-closed no_trade")


async def audit(profile_selector:str,user_sub:str|None)->int:
    gate=Gate();path=Path(settings.sqlite_path)
    if not path.is_file():gate.fail("database","configured SQLite database does not exist");return gate.print()
    try:
        with _connect_readonly(path) as db:
            required={"users","broker_connections","broker_accounts","strategy_templates","execution_profiles","autonomous_order_previews",
                "broker_submissions","autonomous_decision_runs","autonomous_schedules","autonomous_schedule_dispatches","autonomous_worker_heartbeats"}
            required.add("operational_controls")
            present={item["name"] for item in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            profile_columns={item["name"] for item in db.execute("PRAGMA table_info(execution_profiles)")}
            required_profile_columns={"autonomous_armed","armed_until","autonomous_shadow_mode","decision_provider","model_identifier"}
            migrations_current=required<=present and required_profile_columns<=profile_columns
            gate.add("migrations",migrations_current,"required tables and columns are present","one or more required tables or columns are missing")
            if not migrations_current:return gate.print()
            row=_profile(db,profile_selector,user_sub);risk=json.loads(row["risk_json"]);strategy=json.loads(row["strategy_config_json"])
            watermark=db.execute("SELECT MAX(equity_high_watermark) high FROM execution_settings WHERE user_sub=? AND account_id=? AND acc_num=?",
                (row["auth0_sub"],row["broker_account_id"],row["acc_num"])).fetchone()
            risk["_equity_high_watermark"]=watermark["high"] if watermark else None
            demo=row["account_environment"]=="demo" and row["connection_environment"]=="demo" and row["is_demo"]==1 and row["base_url"].rstrip("/")==settings.tradelocker_demo_base_url.rstrip("/")
            gate.add("demo_binding",demo,"profile is immutably bound to a classified demo account","profile/account/connection is not confirmed demo")
            active=row["available"] and row["broker_active"] and row["locally_enabled"] and row["connection_status"]=="active"
            gate.add("account_availability",bool(active),"stored account and connection are available","stored account or connection is disabled/unavailable")
            gate.add("execution_mode",row["enabled"] and row["execution_mode"]=="demo_autonomous","profile is enabled in demo_autonomous mode","profile is disabled or not demo_autonomous")
            try:armed_until=datetime.fromisoformat(row["armed_until"] or "");armed=bool(row["autonomous_armed"]) and armed_until>datetime.now(timezone.utc)
            except (TypeError,ValueError):armed=False
            gate.add("arming",armed,"profile is armed with an unexpired deadline","profile is disarmed or arming has expired")
            control=db.execute("SELECT value FROM operational_controls WHERE key='kill_switch'").fetchone()
            kill_switch=settings.kill_switch_enabled if control is None else control["value"]=="enabled"
            gate.add("kill_switch",not kill_switch,"kill switch is disabled","kill switch is enabled")
            expected={"risk_per_trade_percent":.25,"maximum_open_positions":1,"maximum_pending_orders":1,"maximum_new_entries_per_day":2,"daily_loss_limit_percent":3,"drawdown_cutoff_percent":10}
            gate.add("risk_policy",all(float(risk.get(key,-1))==value for key,value in expected.items()),"required risk limits match", "required risk limits do not match")
            today=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
            entries=db.execute("SELECT COUNT(*) count FROM autonomous_runs WHERE user_sub=? AND account_id=? AND decision='submit' AND result_status IN ('verified','unknown') AND created_at>=?",(row["auth0_sub"],row["broker_account_id"],today)).fetchone()["count"]
            gate.add("daily_entry_limit",entries<int(risk.get("maximum_new_entries_per_day",2)),"daily entry capacity is available","daily entry limit has been reached")
            manual=db.execute("SELECT result_status FROM autonomous_runs WHERE user_sub=? AND account_id=? AND decision='submit' ORDER BY created_at DESC LIMIT 1",(row["auth0_sub"],row["broker_account_id"])).fetchone()
            gate.add("last_manual_execution",bool(manual and manual["result_status"]=="verified"),"last demo submission is verified","no verified manual demo submission is recorded")
            shadow=db.execute("SELECT state FROM autonomous_decision_runs WHERE user_sub=? AND profile_ref=? AND state='shadow_trade' ORDER BY created_at DESC LIMIT 1",(row["auth0_sub"],row["public_id"])).fetchone()
            gate.add("last_shadow_run",shadow is not None,"a shadow autonomous TRADE is recorded","no shadow autonomous TRADE is recorded")
            schedule=db.execute("SELECT enabled,next_run_at FROM autonomous_schedules WHERE user_sub=? AND profile_ref=?",(row["auth0_sub"],row["public_id"])).fetchone()
            gate.add("schedule",bool(schedule and schedule["enabled"] and schedule["next_run_at"]),"enabled schedule has a next UTC run","enabled schedule with next run is missing")
            heartbeat=db.execute("SELECT status,last_heartbeat_at FROM autonomous_worker_heartbeats ORDER BY last_heartbeat_at DESC LIMIT 1").fetchone()
            healthy=bool(heartbeat and heartbeat["status"]=="running" and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat["last_heartbeat_at"])).total_seconds()<=settings.autonomous_scheduler_heartbeat_stale_seconds)
            gate.add("scheduler_health",healthy,"scheduler heartbeat is healthy","scheduler heartbeat is missing or stale")
        await _network_checks(gate,row,risk,strategy)
    except Exception as exc:gate.fail("readiness",f"sanitized readiness failure ({type(exc).__name__})")
    return gate.print()


def main()->int:
    parser=argparse.ArgumentParser(description="Read-only autonomous TradeLocker demo readiness gate")
    parser.add_argument("--profile",required=True,help="Safe execution-profile name or public reference")
    parser.add_argument("--user",help="Auth subject only when the profile selector is not globally unique")
    args=parser.parse_args();return asyncio.run(audit(args.profile,args.user))


if __name__=="__main__":raise SystemExit(main())
