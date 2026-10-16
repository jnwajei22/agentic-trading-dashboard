from __future__ import annotations

import asyncio
import logging
import signal
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config.settings import settings
from app.services.autonomous.runner import AutonomousDecisionRunner, SAFE_PRE_SUBMIT_RETRY_REASONS
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository, ScheduleStorageError, utcnow
from app.services.trading_policy import classify_market_group


logger=logging.getLogger(__name__)
DEFAULT_TIMEZONE="America/Chicago"
DEFAULT_LOCAL_TIMES=("05:00","07:00","09:00","11:00","13:15")
WEEKDAYS=("monday","tuesday","wednesday","thursday","friday","saturday","sunday")
MARKET_SESSION_TIMES={"asia":["19:00"],"sydney":["17:00"],"tokyo":["19:00"],
                      "london":["03:00"],"new_york":["08:00"],"london_new_york_overlap":["08:00"],"overlap":["08:00"]}


async def dispatch_autonomous_cycle(runner:AutonomousDecisionRunner,user_sub:str,profile_ref:str,
                                    run_key:str,trigger_reason:str,*,allow_safe_retry:bool=False)->dict[str,Any]:
    """Shared durable-run dispatch used by both scheduled and explicit immediate cycles."""
    return await runner.run(user_sub,profile_ref,run_key,trigger_reason,allow_safe_retry=allow_safe_retry)


def validate_local_times(values:list[str])->list[str]:
    normalized=[]
    for value in values:
        try:parsed=datetime.strptime(value,"%H:%M").time()
        except ValueError:raise ScheduleStorageError("Schedule times must use 24-hour HH:MM format.") from None
        normalized.append(f"{parsed.hour:02d}:{parsed.minute:02d}")
    result=sorted(set(normalized))
    if not result or len(result)>24:raise ScheduleStorageError("A schedule requires between 1 and 24 unique times.")
    return result


def normalize_recurrence(value:dict[str,Any]|None,legacy_times:list[str]|None=None)->dict[str,Any]:
    raw=dict(value or {});kind=str(raw.get("type") or "daily")
    if kind not in {"market_session","recurring_interval","daily","weekly","custom"}:
        raise ScheduleStorageError("Schedule type is not supported.")
    days=[str(day).lower() for day in raw.get("days",[]) if str(day).lower() in WEEKDAYS]
    if kind in {"weekly","custom"} and not days:raise ScheduleStorageError("Weekly and custom schedules require at least one day.")
    if kind=="market_session":
        sessions=[str(item).lower() for item in raw.get("sessions",["london"])]
        unknown=[item for item in sessions if item not in MARKET_SESSION_TIMES]
        if unknown:raise ScheduleStorageError("A market session is not supported.")
        times=validate_local_times([time for session in sessions for time in MARKET_SESSION_TIMES[session]])
        return {"type":kind,"sessions":sessions,"days":days or list(WEEKDAYS[:5]),"times":times,
                "market_aware":bool(raw.get("market_aware",True))}
    if kind=="recurring_interval":
        start=str(raw.get("start_time") or "08:00");end=str(raw.get("end_time") or "17:00")
        validate_local_times([start,end]);minutes=int(raw.get("interval_minutes") or 60)
        if minutes<15 or minutes>1440:raise ScheduleStorageError("Interval must be between 15 and 1440 minutes.")
        first=datetime.strptime(start,"%H:%M");last=datetime.strptime(end,"%H:%M")
        if last<first:raise ScheduleStorageError("The interval end time must be after its start time.")
        times=[];current=first
        while current<=last and len(times)<96:
            times.append(current.strftime("%H:%M"));current+=timedelta(minutes=minutes)
        return {"type":kind,"start_time":start,"end_time":end,"interval_minutes":minutes,
                "days":days or list(WEEKDAYS[:5]),"times":times,"market_aware":bool(raw.get("market_aware",True))}
    times=validate_local_times(list(raw.get("times") or legacy_times or DEFAULT_LOCAL_TIMES))
    return {"type":kind,"times":times,"days":days or (list(WEEKDAYS) if kind=="daily" else list(WEEKDAYS[:5])),
            "market_aware":bool(raw.get("market_aware",True))}


def _recurrence(schedule:dict[str,Any])->dict[str,Any]:
    expression=schedule.get("expression") or {}
    return normalize_recurrence(expression.get("recurrence"),expression.get("times"))


def _valid_instants(day:date,local_time:str,zone:ZoneInfo)->list[datetime]:
    parsed=time.fromisoformat(local_time);naive=datetime.combine(day,parsed);results=[]
    for fold in (0,1):
        local=naive.replace(tzinfo=zone,fold=fold);utc=local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None)==naive and utc not in results:results.append(utc)
    return sorted(results)


def next_scheduled_utc(after:datetime,timezone_name:str,local_times:list[str])->datetime:
    if after.tzinfo is None:raise ValueError("after must be timezone-aware")
    try:zone=ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:raise ScheduleStorageError("The schedule timezone is not a valid IANA timezone.") from None
    times=validate_local_times(local_times);local_after=after.astimezone(zone)
    for offset in range(0,9):
        day=local_after.date()+timedelta(days=offset)
        candidates=[instant for item in times for instant in _valid_instants(day,item,zone)]
        future=[candidate for candidate in sorted(candidates) if candidate>after.astimezone(timezone.utc)]
        if future:return future[0]
    raise ScheduleStorageError("Unable to calculate the next schedule occurrence.")


def next_recurrence_utc(after:datetime,timezone_name:str,recurrence:dict[str,Any])->datetime:
    normalized=normalize_recurrence(recurrence)
    try:zone=ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:raise ScheduleStorageError("The schedule timezone is not a valid IANA timezone.") from None
    local_after=after.astimezone(zone);allowed=set(normalized["days"])
    for offset in range(15):
        day=local_after.date()+timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in allowed:continue
        candidates=[instant for item in normalized["times"] for instant in _valid_instants(day,item,zone)]
        future=[candidate for candidate in sorted(candidates) if candidate>after.astimezone(timezone.utc)]
        if future:return future[0]
    raise ScheduleStorageError("Unable to calculate the next schedule occurrence.")


def _likely_open(profile:dict[str,Any],instant:datetime)->bool:
    universe=(profile.get("profile_v2") or {}).get("market_universe") or {}
    groups=set(universe.get("groups") or [])
    if universe.get("mode")=="custom":
        groups={classify_market_group(str(value))[0] for value in universe.get("included_instrument_ids",[])
                if not str(value).isdigit()}
    if not groups or "crypto" in groups:return True
    current=instant.astimezone(timezone.utc)
    if any(group.startswith("forex_") for group in groups):
        return not (current.weekday()==5 or (current.weekday()==6 and current.hour<22) or (current.weekday()==4 and current.hour>=22))
    return current.weekday()<5


def next_likely_eligible_utc(after:datetime,timezone_name:str,local_times:list[str],profile:dict[str,Any])->datetime:
    candidate=after
    for _ in range(14*max(1,len(local_times))):
        candidate=next_scheduled_utc(candidate,timezone_name,local_times)
        if _likely_open(profile,candidate):return candidate
    return candidate


class AutonomousScheduleService:
    def __init__(self,*,schedules:ScheduleRepository|None=None,brokers:BrokerRepository|None=None,
                 execution:ExecutionRepository|None=None)->None:
        self.schedules=schedules or ScheduleRepository();self.brokers=brokers or BrokerRepository()
        self.execution=execution or ExecutionRepository()

    def _profile(self,user_sub:str,profile_ref:str)->dict[str,Any]:
        profile=next((item for item in self.brokers.list_profiles(user_sub) if item["public_id"]==profile_ref),None)
        if not profile:raise ScheduleStorageError("Execution profile was not found.")
        if profile.get("account_environment") not in {"demo","live"} or profile.get("is_demo") not in {0,1}:
            raise ScheduleStorageError("The profile account environment is not verified.")
        return profile

    def save(self,user_sub:str,profile_ref:str,*,timezone_name:str=DEFAULT_TIMEZONE,
             local_times:list[str]|None=None,recurrence:dict[str,Any]|None=None,enabled:bool=True,
             maximum_lateness_seconds:int=600)->dict[str,Any]:
        profile=self._profile(user_sub,profile_ref);rule=normalize_recurrence(recurrence,local_times)
        times=rule["times"]
        if not 30<=maximum_lateness_seconds<=3600:raise ScheduleStorageError("Maximum lateness must be between 30 and 3600 seconds.")
        next_run=next_recurrence_utc(utcnow(),timezone_name,rule).isoformat() if enabled else None
        return self._present(self.schedules.upsert_schedule(user_sub,profile_ref,timezone_name=timezone_name,
            local_times=times,recurrence=rule,enabled=enabled,next_run_at=next_run,
            maximum_lateness_seconds=maximum_lateness_seconds),profile)

    def set_enabled(self,user_sub:str,schedule_id:str,enabled:bool)->dict[str,Any]:
        schedule=self.schedules.get_schedule(user_sub,schedule_id)
        if not schedule:raise ScheduleStorageError("Autonomous schedule was not found.")
        profile=self._profile(user_sub,schedule["profile_ref"])
        next_run=next_recurrence_utc(utcnow(),schedule["timezone"],_recurrence(schedule)).isoformat() if enabled else None
        self.schedules.set_enabled(user_sub,schedule_id,enabled,next_run)
        return self._present(self.schedules.get_schedule(user_sub,schedule_id) or {},profile)

    def list(self,user_sub:str)->list[dict[str,Any]]:
        results=[]
        for item in self.schedules.list_schedules(user_sub):
            recent=self.schedules.list_dispatches(user_sub,1,item["profile_ref"])
            profile=self._profile(user_sub,item["profile_ref"])
            results.append({**self._present(item,profile),"readiness_warnings":self._readiness_warnings(user_sub,item,profile),
                            "latest_dispatch":recent[0] if recent else None})
        return results

    def status(self,user_sub:str,schedule_id:str)->dict[str,Any]:
        schedule=self.schedules.get_schedule(user_sub,schedule_id)
        if not schedule:raise ScheduleStorageError("Autonomous schedule was not found.")
        dispatches=self.schedules.list_dispatches(user_sub,10,schedule["profile_ref"])
        profile=self._profile(user_sub,schedule["profile_ref"])
        return {**self._present(schedule,profile),"readiness_warnings":self._readiness_warnings(user_sub,schedule,profile),
                "recent_runs":dispatches}

    def _readiness_warnings(self,user_sub:str,schedule:dict[str,Any],profile:dict[str,Any])->list[dict[str,str]]:
        warnings=[];controls=self.execution.get_autonomous_controls(user_sub)
        if not profile.get("enabled"):warnings.append({"code":"strategy_disabled","message":"The associated strategy is disabled."})
        if not profile.get("account_available") or not profile.get("locally_enabled"):
            warnings.append({"code":"account_unavailable","message":"The trading account is unavailable."})
        if controls.get("global_autonomous_kill_switch"):
            warnings.append({"code":"kill_switch_active","message":"The global kill switch blocks scheduled submissions."})
        universe=(profile.get("profile_v2") or {}).get("market_universe") or {}
        if universe.get("mode")=="custom" and not universe.get("included_instrument_ids"):
            warnings.append({"code":"no_market_selected","message":"The strategy has no selected markets."})
        limit=((profile.get("profile_v2") or {}).get("risk_policy") or {}).get("maximum_new_entries_per_day")
        if limit and len(_recurrence(schedule)["times"])>int(limit):
            warnings.append({"code":"daily_entry_limit","message":"Scheduled checks exceed the strategy daily entry limit."})
        if ScheduleRepository().worker_health()["status"]!="healthy":
            warnings.append({"code":"automation_unavailable","message":"Automation services need attention."})
        return warnings

    def daily_summary(self,user_sub:str,day:date|None=None)->dict[str,Any]:
        target=day or utcnow().date();start=datetime.combine(target,time.min,tzinfo=timezone.utc);end=start+timedelta(days=1)
        runs=[item for item in self.execution.recent_decision_runs(user_sub,200)
              if start<=datetime.fromisoformat(item["created_at"])<end]
        outcomes={"TRADE":0,"NO_TRADE":0,"BLOCKED":0,"MARKET_CLOSED":0,"SKIPPED":0,"ERROR":0}
        details=[]
        for item in runs:
            public=AutonomousDecisionRunner._public_run(item);outcome=public["outcome"]
            if outcome in outcomes:outcomes[outcome]+=1
            decision=item.get("decision") or {};context=item.get("context") or {};account=context.get("account",{});risk=context.get("risk_state",{})
            details.append({"run_id":item["id"],"run_time":item["created_at"],"outcome":outcome,
                "symbol":decision.get("symbol"),"side":decision.get("side"),"execution_id":item.get("execution_id"),
                "reasons":item.get("reason_codes",[]),"balance":account.get("balance"),"equity":account.get("equity"),
                "daily_pnl":risk.get("daily_realized_pnl"),"open_pnl":risk.get("open_pnl")})
        profiles=self.brokers.list_profiles(user_sub)
        entry_count=sum(1 for item in runs if item.get("state")=="trade" and item.get("execution_id"))
        controls=self.execution.get_autonomous_controls(user_sub)
        return {"schema_version":"1.0","date":target.isoformat(),"timezone":"UTC","outcomes":outcomes,
            "daily_entry_count":entry_count,"kill_switch":controls["global_autonomous_kill_switch"],
            "autonomous_controls":controls,"armed_profiles":0,"armed_profiles_deprecated":True,"runs":details}

    @staticmethod
    def _present(schedule:dict[str,Any],profile:dict[str,Any]|None=None)->dict[str,Any]:
        result={**schedule};zone=ZoneInfo(schedule["timezone"])
        recurrence=_recurrence(schedule);result["schedule_type"]=recurrence["type"];result["recurrence"]=recurrence
        for key in ("next_run_at","last_run_at"):
            result[f"{key}_local"]=datetime.fromisoformat(schedule[key]).astimezone(zone).isoformat() if schedule.get(key) else None
        result["next_scheduled_check"]=schedule.get("next_run_at")
        previews=[];cursor=utcnow()
        if schedule.get("enabled"):
            for _ in range(5):cursor=next_recurrence_utc(cursor,schedule["timezone"],recurrence);previews.append(cursor.isoformat())
        result["next_run_times"]=previews
        result["next_eligible_trading_run"]=(next_likely_eligible_utc(utcnow(),schedule["timezone"],recurrence["times"],profile).isoformat()
            if schedule.get("enabled") and profile else None)
        return result


class AutonomousSchedulerWorker:
    def __init__(self,*,worker_id:str|None=None,schedules:ScheduleRepository|None=None,
                 runner_factory:Callable[[],AutonomousDecisionRunner]|None=None)->None:
        self.worker_id=worker_id or f"worker_{uuid4().hex[:12]}";self.schedules=schedules or ScheduleRepository()
        self.runner_factory=runner_factory or AutonomousDecisionRunner;self._stop=asyncio.Event()

    @staticmethod
    def _next(schedule:dict[str,Any],after:datetime)->datetime:
        return next_recurrence_utc(after,schedule["timezone"],_recurrence(schedule))

    async def run_once(self,now:datetime|None=None)->dict[str,int]:
        current=now or utcnow();self.schedules.heartbeat(self.worker_id,"running",{"phase":"poll"})
        dispatches=self.schedules.claim_due(worker_id=self.worker_id,now=current,
            lease_seconds=settings.autonomous_scheduler_lease_seconds,limit=settings.autonomous_scheduler_batch_size,next_run=self._next)
        counts={"due":len(dispatches),"completed":0,"retrying":0,"skipped":0}
        logger.info("autonomous_scheduler_poll worker_id=%s due_schedules=%s",self.worker_id,len(dispatches))
        for dispatch in dispatches:
            if not self.schedules.mark_running(dispatch["id"],self.worker_id,settings.autonomous_scheduler_lease_seconds):
                counts["skipped"]+=1;logger.info("autonomous_scheduler_lock_contention worker_id=%s dispatch_id=%s",self.worker_id,dispatch["id"]);continue
            try:
                result=await dispatch_autonomous_cycle(self.runner_factory(),dispatch["user_sub"],dispatch["profile_ref"],
                    dispatch["run_key"],f"scheduled:{dispatch['id']}",allow_safe_retry=True)
            except Exception:
                result={"status":"error","outcome":"ERROR","reason_codes":["scheduler_runner_failure"],"run_id":None,
                    "preview_id":None,"execution_id":None}
            if result.get("outcome")=="RUNNING":
                stalled=result.get("status")
                reason="submission_reconciliation_required" if stalled=="submitting" else "stale_preview_requires_review"
                result={**result,"status":"error","outcome":"ERROR","reason_codes":[reason]}
            reasons=result.get("reason_codes") or [];reason=reasons[0] if reasons else None
            safe=not result.get("preview_id") and not result.get("execution_id") and bool(set(reasons).intersection(SAFE_PRE_SUBMIT_RETRY_REASONS|{"scheduler_runner_failure"}))
            retries=int(dispatch.get("retry_count",0))
            if safe and retries<settings.autonomous_scheduler_max_retries:
                retries+=1;delay=min(settings.autonomous_scheduler_retry_cap_seconds,
                    settings.autonomous_scheduler_retry_base_seconds*(2**(retries-1)))
                next_retry=current+timedelta(seconds=delay)
                suggested=(result.get("validation") or {}).get("suggested_retry_at")
                if suggested:
                    try: next_retry=max(next_retry,datetime.fromisoformat(suggested))
                    except ValueError: pass
                self.schedules.finish_dispatch(dispatch["id"],state="retry_wait",outcome=result.get("outcome"),run_id=result.get("run_id"),
                    reason_code=reason,summary=result,safe_retry=True,next_retry_at=next_retry.isoformat(),retry_count=retries)
                counts["retrying"]+=1
            elif result.get("status")=="skipped":
                self.schedules.finish_dispatch(dispatch["id"],state="skipped",outcome=result.get("outcome"),run_id=result.get("run_id"),
                    reason_code=reason,summary=result,safe_retry=False,retry_count=retries)
                counts["skipped"]+=1
            else:
                state="retry_exhausted" if safe else "completed"
                self.schedules.finish_dispatch(dispatch["id"],state=state,outcome=result.get("outcome"),run_id=result.get("run_id"),
                    reason_code=reason,summary=result,safe_retry=safe,retry_count=retries)
                counts["completed"]+=1
            logger.info("autonomous_scheduler_outcome worker_id=%s dispatch_id=%s profile_ref=%s outcome=%s reason=%s retry_count=%s",
                self.worker_id,dispatch["id"],dispatch["profile_ref"],result.get("outcome"),reason,retries)
        self.schedules.heartbeat(self.worker_id,"running",counts);return counts

    async def run_forever(self)->None:
        self.schedules.heartbeat(self.worker_id,"running",{"phase":"startup"})
        try:
            while not self._stop.is_set():
                await self.run_once()
                try:await asyncio.wait_for(self._stop.wait(),timeout=settings.autonomous_scheduler_poll_seconds)
                except TimeoutError:pass
        finally:self.schedules.heartbeat(self.worker_id,"stopped",{"phase":"shutdown"})

    def stop(self)->None:self._stop.set()


async def _main()->None:
    worker=AutonomousSchedulerWorker();loop=asyncio.get_running_loop()
    for event in (signal.SIGINT,signal.SIGTERM):
        try:loop.add_signal_handler(event,worker.stop)
        except NotImplementedError:pass
    await worker.run_forever()


if __name__=="__main__":asyncio.run(_main())
