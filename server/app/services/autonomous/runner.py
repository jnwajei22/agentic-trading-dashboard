from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

from app.config.settings import settings
from app.models.autonomous import AutonomousOrderProposal
from app.services.autonomous.context import build_decision_context
from app.services.autonomous.decision import DecisionAction, DecisionProvider, decision_provider_readiness, production_provider
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository, utcnow
from app.brokers.tradelocker.client import TradeLockerError
from app.services.trading_policy import market_is_open, normalize_instrument, resolve_universe


FINAL_STATES={"trade","no_trade","blocked","market_closed","error","skipped"}
SAFE_PRE_SUBMIT_RETRY_REASONS={"provider_unavailable","market_snapshot_unavailable","broker_unreachable",
    "account_mapping_unavailable","account_status_unavailable","runner_internal_error",
    "tradelocker_rate_limit_exhausted"}
DRY_RUN_IGNORED_REASONS={"profile_disabled","global_autonomous_kill_switch_enabled",
    "demo_autonomous_disabled","live_autonomous_disabled","live_autonomous_execution_not_implemented"}
logger=logging.getLogger(__name__)


class AutonomousDecisionRunner:
    def __init__(self,*,brokers:BrokerRepository|None=None,execution:ExecutionRepository|None=None,
                 demo:AutonomousDemoService|None=None,provider:DecisionProvider|None=None)->None:
        self.brokers=brokers or BrokerRepository();self.execution=execution or ExecutionRepository()
        self.demo=demo or AutonomousDemoService(broker_repository=self.brokers,execution_repository=self.execution)
        self.provider_override=provider

    def _profile(self,user_sub:str,profile_ref:str)->dict[str,Any]:
        profile=next((item for item in self.brokers.list_profiles(user_sub) if item["public_id"]==profile_ref),None)
        if not profile:raise AutonomousExecutionError("profile_not_found","The autonomous profile was not found.")
        return profile

    @staticmethod
    def _session(now:datetime)->set[str]:
        hour=now.hour; active=set()
        if 7<=hour<16:active.add("london")
        if 12<=hour<21:active.add("new_york")
        if 12<=hour<16:active.add("overlap")
        return active

    def _blocking_reasons(self,profile:dict[str,Any],now:datetime,user_sub:str|None=None)->list[str]:
        reasons=[]
        if not profile.get("enabled"):reasons.append("profile_disabled")
        if not profile.get("account_available",True) or not profile.get("broker_active",True) or not profile.get("locally_enabled",True):
            reasons.append("account_unavailable")
        controls=self.execution.get_autonomous_controls(user_sub) if user_sub else {
            "global_autonomous_kill_switch":self.execution.kill_switch_enabled(),
            "demo_autonomous_enabled":False,"live_autonomous_enabled":False}
        if controls["global_autonomous_kill_switch"]:reasons.append("global_autonomous_kill_switch_enabled")
        environment=profile.get("account_environment")
        if environment=="demo":
            if profile.get("is_demo")!=1:reasons.append("account_environment_unverified")
            if not controls["demo_autonomous_enabled"]:reasons.append("demo_autonomous_disabled")
        elif environment=="live":
            if profile.get("is_demo")!=0:reasons.append("account_environment_unverified")
            if not controls["live_autonomous_enabled"]:reasons.append("live_autonomous_disabled")
            else:reasons.append("live_autonomous_execution_not_implemented")
        else:reasons.append("account_environment_unverified")
        readiness=decision_provider_readiness(profile.get("decision_provider"),profile.get("model_identifier"))
        reasons.extend(readiness["blocking_reasons"])
        return list(dict.fromkeys(reasons))

    async def _market_preflight(self,user_sub:str,profile:dict[str,Any],now:datetime,*,autonomous:bool=True)->dict[str,Any]:
        context=await self.demo.context(user_sub,profile["public_id"],allow_autonomous=autonomous)
        client=self.demo._client(context.connection,context)
        try: payload=await client.get_symbols()
        except TradeLockerError: raise AutonomousExecutionError("market_universe_unavailable","The account market universe is unavailable.",reasons=["market_universe_unavailable"]) from None
        finally: await client.aclose()
        instruments=[item for row in client._instrument_rows(payload) if (item:=normalize_instrument(row)) is not None]
        universe=(profile.get("profile_v2") or {}).get("market_universe") or {"mode":"custom","included_instrument_ids":profile.get("allowed_instruments",[])}
        selected=resolve_universe(instruments,universe)
        # During legacy symbol migration, match symbols as well as stable IDs.
        if universe.get("mode")=="custom" and not selected:
            wanted={str(value).replace("/","").upper() for value in universe.get("included_instrument_ids",[])}
            selected=[item for item in instruments if item["broker_symbol"].replace("/","").upper() in wanted]
        opened=[item for item in selected if market_is_open(item,now)]
        return {"selected_market_universe":[item["instrument_id"] for item in selected],
            "markets_currently_open":[item["instrument_id"] for item in opened],"open":bool(opened)}

    @staticmethod
    def _value(item:Any,names:set[str])->Any:
        if isinstance(item,dict):
            for key,value in item.items():
                if key.lower() in names and value is not None:return value
            for value in item.values():
                found=AutonomousDecisionRunner._value(value,names)
                if found is not None:return found
        return None

    @classmethod
    def _loss_cooldown_active(cls,snapshot:dict[str,Any],minutes:int,now:datetime)->bool:
        for row in reversed(snapshot.get("recent_order_history",[])):
            pnl=cls._value(row,{"pnl","netpnl","profit","realizedpnl"})
            stamp=cls._value(row,{"closetime","closedat","updatedat","timestamp","filledat"})
            try:
                if float(pnl)>=0:continue
                if isinstance(stamp,(int,float)):
                    closed=datetime.fromtimestamp(float(stamp)/(1000 if float(stamp)>10_000_000_000 else 1),timezone.utc)
                else:closed=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
                if closed.tzinfo is None:closed=closed.replace(tzinfo=timezone.utc)
                return now-closed<timedelta(minutes=minutes)
            except (TypeError,ValueError,OverflowError):continue
        return False

    async def status(self,user_sub:str,profile_ref:str)->dict[str,Any]:
        profile=self._profile(user_sub,profile_ref);now=utcnow();reasons=self._blocking_reasons(profile,now,user_sub)
        latest=self.execution.get_decision_run(user_sub,profile_ref=profile_ref)
        controls=self.execution.get_autonomous_controls(user_sub)
        environment=profile.get("account_environment")
        return {"schema_version":"1.0","status":"ready" if not reasons else "blocked","profile_ref":profile_ref,
            "account_alias":profile.get("account_alias"),"confirmed_demo":profile.get("is_demo")==1,
            "environment":environment,"global_kill_switch":controls["global_autonomous_kill_switch"],
            "demo_autonomous_enabled":controls["demo_autonomous_enabled"],
            "live_autonomous_enabled":controls["live_autonomous_enabled"],
            "profile_enabled":profile.get("enabled",False),"account_available":profile.get("account_available",False),
            "autonomous_active":not reasons,
            "execution_mode":profile.get("execution_mode"),"execution_mode_deprecated":True,
            "armed":None,"armed_until":None,"shadow_mode":None,"legacy_controls_deprecated":True,
            "decision_provider":profile.get("decision_provider"),"model_identifier":profile.get("model_identifier"),
            "provider_readiness":decision_provider_readiness(profile.get("decision_provider"),profile.get("model_identifier")),
            "minimum_confidence":profile.get("minimum_confidence"),"kill_switch":controls["global_autonomous_kill_switch"],
            "blocking_reasons":reasons,"latest_run":self._public_run(latest) if latest else None}

    async def snapshot(self,user_sub:str,profile_ref:str)->dict[str,Any]:
        profile=self._profile(user_sub,profile_ref);reasons=self._blocking_reasons(profile,utcnow(),user_sub)
        if reasons:raise AutonomousExecutionError("autonomous_profile_blocked","The autonomous profile is blocked.",reasons=reasons)
        raw=await self.demo.snapshot(user_sub,profile_ref,autonomous=True)
        context,digest=build_decision_context(raw,profile)
        return {"schema_version":"1.0","status":"ok","snapshot_id":raw["snapshot_id"],"context_hash":digest,
            "profile_ref":profile_ref,"decision_context":context,"expires_at":raw["expires_at"]}

    async def run(self,user_sub:str,profile_ref:str,run_key:str,trigger_reason:str,*,allow_safe_retry:bool=False,
                  dry_run:bool=False)->dict[str,Any]:
        if not 8<=len(run_key)<=128 or not 1<=len(trigger_reason)<=200:
            raise AutonomousExecutionError("invalid_run_request","run_key or trigger_reason is invalid.",status="rejected")
        profile=self._profile(user_sub,profile_ref);now=utcnow();run_id=f"adrun_{uuid4().hex}"
        claimed,record=self.execution.claim_decision_run({"id":run_id,"run_key":run_key,"user_sub":user_sub,"profile_ref":profile_ref,
            "strategy_ref":profile.get("strategy_template_id") or "unknown","strategy_version":profile.get("strategy_version") or "unknown",
            "decision_provider":profile.get("decision_provider") or "no_trade","model_identifier":profile.get("model_identifier"),
            "trigger_reason":trigger_reason,"state":"claimed","shadow_mode":dry_run,
            "started_at":now.isoformat(),"created_at":now.isoformat(),"updated_at":now.isoformat()})
        if not claimed:
            state=record.get("state")
            reasons=set(record.get("reason_codes") or [])
            resumable=state in {"claimed","snapshotting","deciding","validating"}
            retryable=state in {"no_trade","blocked","error"} and bool(reasons.intersection(SAFE_PRE_SUBMIT_RETRY_REASONS))
            if not allow_safe_retry or record.get("preview_id") or record.get("execution_id") or not (resumable or retryable):
                logger.info("autonomous_run_duplicate_suppressed profile_ref=%s run_id=%s state=%s",profile_ref,record.get("id"),state)
                return self._public_run(record,duplicate=True)
            run_id=record["id"]
            self.execution.update_decision_run(run_id,state="claimed",completed_at=None,reason_codes_json=[],
                validation_json={},decision_json={})
        try:
            reasons=self._blocking_reasons(profile,now,user_sub)
            if dry_run:
                reasons=[reason for reason in reasons if reason not in DRY_RUN_IGNORED_REASONS]
            if reasons:return self._finish(run_id,"skipped",reasons)
            if isinstance(self.demo,AutonomousDemoService):
                market=await self._market_preflight(user_sub,profile,now,autonomous=not dry_run)
                if not market["open"]:
                    return self._finish(run_id,"market_closed",["no_selected_market_open"],validation_json=market)
            self.execution.update_decision_run(run_id,state="snapshotting")
            snapshot=await self.demo.snapshot(user_sub,profile_ref,autonomous=not dry_run)
            if self._loss_cooldown_active(snapshot,int(profile.get("cooldown_minutes_after_loss") or 60),utcnow()):
                return self._finish(run_id,"blocked",["loss_cooldown_active"])
            context,digest=build_decision_context(snapshot,profile)
            self.execution.update_decision_run(run_id,state="deciding",snapshot_id=snapshot["snapshot_id"],context_hash=digest,
                context_json=context,account_record_id=snapshot.get("account_ref"),connection_ref=snapshot.get("connection_ref"))
            provider=self.provider_override or production_provider(profile.get("decision_provider") or "no_trade",profile.get("model_identifier"))
            result=await provider.decide(context);decision=result.decision
            self.execution.update_decision_run(run_id,state="validating",decision_json=decision.model_dump(mode="json"),
                usage_json=result.usage,provider_latency_ms=result.latency_ms,model_identifier=result.model_identifier or profile.get("model_identifier"))
            if decision.action!=DecisionAction.TRADE:
                state="blocked" if decision.action==DecisionAction.BLOCKED else "error" if decision.action==DecisionAction.ERROR else "no_trade"
                return self._finish(run_id,state,decision.reason_codes)
            validation=[]
            if decision.confidence<float(profile.get("minimum_confidence") or settings.autonomous_default_minimum_confidence):validation.append("confidence_below_threshold")
            if decision.symbol not in snapshot.get("market",{}).get("pairs",{}):validation.append("pair_not_allowed")
            pair_context=context.get("market",{}).get(decision.symbol or "",{})
            if not pair_context.get("complete") or any(not item.get("complete") for item in pair_context.get("timeframes",{}).values()):validation.append("market_data_incomplete")
            if snapshot.get("news_blackouts"):validation.append("news_blackout")
            risk_blockers=snapshot.get("risk_state",{}).get("blocking_reasons") or []
            validation.extend(str(reason) for reason in risk_blockers)
            if not snapshot.get("execution_eligibility") and not risk_blockers:validation.append("execution_ineligible")
            if validation:
                self.execution.update_decision_run(run_id,validation_json={"approved":False,"reasons":validation})
                return self._finish(run_id,"blocked",validation)
            if dry_run:
                self.execution.update_decision_run(run_id,validation_json={"approved":True,"reasons":[],
                    "dry_run":True,"would_submit":False,"would_trade":True})
                return self._finish(run_id,"no_trade",["dry_run_trade_candidate"])
            proposal=AutonomousOrderProposal(snapshot_id=snapshot["snapshot_id"],pair=decision.symbol or "",side=decision.side,
                order_type=decision.order_type,entry=decision.entry,stop_loss=decision.stop_loss,take_profit=decision.take_profit,
                reason_codes=decision.reason_codes)
            self.execution.update_decision_run(run_id,state="previewing",validation_json={"approved":True,"reasons":[]})
            reasons=self._blocking_reasons(self._profile(user_sub,profile_ref),utcnow(),user_sub)
            if reasons:return self._finish(run_id,"skipped",reasons)
            preview=await self.demo.review(user_sub,profile_ref,proposal,autonomous=True)
            self.execution.update_decision_run(run_id,preview_id=preview["preview_id"])
            reasons=self._blocking_reasons(self._profile(user_sub,profile_ref),utcnow(),user_sub)
            if reasons:return self._finish(run_id,"skipped",reasons)
            self.execution.update_decision_run(run_id,state="submitting")
            execution=await self.demo.submit(user_sub,preview["preview_id"],f"autonomous:{run_id}")
            return self._finish(run_id,"trade",[str(execution.get("status","submitted"))],execution_id=execution.get("execution_id"),execution_json=execution)
        except AutonomousExecutionError as exc:
            retry_at = None
            for value in (exc.details.get("timeframes") or {}).values():
                candidate = (value.get("metadata") or {}).get("suggested_retry_at")
                if candidate and (retry_at is None or candidate > retry_at): retry_at = candidate
            validation = {"retryable": "tradelocker_rate_limit_exhausted" in exc.reasons,
                          "suggested_retry_at": retry_at}
            return self._finish(run_id,"blocked",exc.reasons,validation_json=validation)
        except Exception:
            return self._finish(run_id,"error",["runner_internal_error"])

    def _finish(self,run_id:str,state:str,reasons:list[str],**updates:Any)->dict[str,Any]:
        self.execution.update_decision_run(run_id,state=state,reason_codes_json=reasons,completed_at=utcnow().isoformat(),**updates)
        record=self.execution.get_decision_run_by_id(run_id)
        logger.info("autonomous_run_outcome run_id=%s profile_ref=%s state=%s provider_latency_ms=%s reasons=%s",
            run_id,record.get("profile_ref") if record else None,state,record.get("provider_latency_ms") if record else None,reasons)
        return self._public_run(record or {"id":run_id,"state":state,"reason_codes":reasons})

    def result(self,user_sub:str,run_id:str)->dict[str,Any]:
        record=self.execution.get_decision_run(user_sub,run_id)
        if not record:raise AutonomousExecutionError("run_not_found","No owned autonomous run was found.",status="not_found")
        return self._public_run(record)

    @staticmethod
    def _public_run(record:dict[str,Any],duplicate:bool=False)->dict[str,Any]:
        state=record.get("state","unknown")
        context=record.get("context") or {};decision=record.get("decision") or {};account=context.get("account") or {};risk=context.get("risk_state") or {}
        outcome="TRADE" if state=="trade" else "NO_TRADE" if state=="no_trade" else "MARKET_CLOSED" if state=="market_closed" else "SKIPPED" if state=="skipped" else "BLOCKED" if state=="blocked" else "ERROR" if state=="error" else "RUNNING"
        return {"schema_version":"1.0","status":state,"outcome":outcome,"run_id":record.get("id"),"run_key":record.get("run_key"),
            "profile_ref":record.get("profile_ref"),"snapshot_id":record.get("snapshot_id"),"context_hash":record.get("context_hash"),
            "decision":decision,"symbol":decision.get("symbol"),"side":decision.get("side"),
            "validation":record.get("validation",{}),"reason_codes":record.get("reason_codes",[]),
            "execution":record.get("execution",{}),
            "account_summary":{"balance":account.get("balance"),"equity":account.get("equity"),"available_funds":account.get("available_funds"),
                "daily_pnl":risk.get("daily_realized_pnl"),"open_pnl":risk.get("open_pnl")},
            "preview_id":record.get("preview_id"),"execution_id":record.get("execution_id"),"shadow_mode":None,
            "provider":record.get("decision_provider"),"model_identifier":record.get("model_identifier"),
            "started_at":record.get("started_at"),"completed_at":record.get("completed_at"),
            "trigger_reason":record.get("trigger_reason"),"dry_run":bool(record.get("shadow_mode")),"duplicate":duplicate}
