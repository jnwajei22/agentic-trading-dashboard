from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import settings
from app.jobs.autonomous_scheduler import (
    DEFAULT_LOCAL_TIMES,
    AutonomousScheduleService,
    AutonomousSchedulerWorker,
    next_scheduled_utc,
    next_recurrence_utc,
    normalize_recurrence,
)


def test_structured_recurrence_supports_all_builder_types():
    after=datetime(2026,7,20,12,tzinfo=timezone.utc)
    values=[
        {"type":"daily","times":["09:00"]},
        {"type":"weekly","days":["monday"],"times":["09:00"]},
        {"type":"custom","days":["monday","wednesday"],"times":["10:00"]},
        {"type":"market_session","sessions":["london"]},
        {"type":"recurring_interval","start_time":"08:00","end_time":"10:00","interval_minutes":60},
    ]
    for value in values:
        normalized=normalize_recurrence(value)
        assert normalized["type"]==value["type"]
        assert next_recurrence_utc(after,"America/Chicago",normalized)>after
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository, ScheduleStorageError


def test_chicago_five_times_and_dst_offsets():
    winter=datetime(2026,1,15,10,59,tzinfo=timezone.utc)
    occurrences=[];cursor=winter
    for _ in range(5):
        cursor=next_scheduled_utc(cursor,"America/Chicago",list(DEFAULT_LOCAL_TIMES));occurrences.append(cursor)
    assert [item.strftime("%H:%M") for item in occurrences]==["11:00","13:00","15:00","17:00","19:15"]
    before_spring=next_scheduled_utc(datetime(2026,3,7,10,tzinfo=timezone.utc),"America/Chicago",["05:00"])
    after_spring=next_scheduled_utc(datetime(2026,3,8,9,tzinfo=timezone.utc),"America/Chicago",["05:00"])
    after_fall=next_scheduled_utc(datetime(2026,11,1,10,tzinfo=timezone.utc),"America/Chicago",["05:00"])
    assert before_spring.hour==11 and after_spring.hour==10 and after_fall.hour==11


def _due(repo:ScheduleRepository,now:datetime,user:str="u",profile:str="p",lateness:int=600):
    return repo.upsert_schedule(user,profile,timezone_name="America/Chicago",local_times=["05:00"],enabled=True,
        next_run_at=(now-timedelta(seconds=1)).isoformat(),maximum_lateness_seconds=lateness)


def _next(schedule,after):return next_scheduled_utc(after,schedule["timezone"],schedule["expression"]["times"])


def test_restart_reclaims_same_dispatch_after_lease_and_duplicate_worker_waits(tmp_path):
    db=tmp_path/"scheduler.db";repo=ScheduleRepository(db);now=datetime(2026,1,15,12,tzinfo=timezone.utc);_due(repo,now)
    first=repo.claim_due(worker_id="one",now=now,lease_seconds=30,limit=10,next_run=_next)
    assert len(first)==1
    assert repo.claim_due(worker_id="two",now=now+timedelta(seconds=10),lease_seconds=30,limit=10,next_run=_next)==[]
    restarted=ScheduleRepository(db)
    recovered=restarted.claim_due(worker_id="two",now=now+timedelta(seconds=31),lease_seconds=30,limit=10,next_run=_next)
    assert len(recovered)==1 and recovered[0]["id"]==first[0]["id"] and recovered[0]["run_key"]==first[0]["run_key"]


def test_stale_schedule_is_misfired_once_and_advanced(tmp_path):
    repo=ScheduleRepository(tmp_path/"scheduler.db");now=datetime(2026,1,15,12,tzinfo=timezone.utc)
    schedule=repo.upsert_schedule("u","p",timezone_name="America/Chicago",local_times=["05:00"],enabled=True,
        next_run_at=(now-timedelta(hours=1)).isoformat(),maximum_lateness_seconds=60)
    assert repo.claim_due(worker_id="one",now=now,lease_seconds=30,limit=10,next_run=_next)==[]
    history=repo.list_dispatches("u")
    assert len(history)==1 and history[0]["state"]=="misfired" and history[0]["reason_code"]=="maximum_lateness_exceeded"
    assert datetime.fromisoformat(repo.get_schedule("u",schedule["id"])["next_run_at"])>now


class FakeRunner:
    def __init__(self,result):self.result=result;self.calls=[]
    async def run(self,*args,**kwargs):self.calls.append((args,kwargs));return self.result


@pytest.mark.asyncio
async def test_worker_runs_once_and_persists_exact_key(tmp_path):
    db=tmp_path/"scheduler.db";repo=ScheduleRepository(db);now=datetime.now(timezone.utc);_due(repo,now)
    fake=FakeRunner({"status":"shadow_trade","outcome":"TRADE","reason_codes":["shadow_mode"],"run_id":"r",
        "preview_id":"preview","execution_id":None})
    restarted=ScheduleRepository(db);worker=AutonomousSchedulerWorker(worker_id="one",schedules=restarted,runner_factory=lambda:fake)
    result=await worker.run_once(now);await worker.run_once(now)
    history=restarted.list_dispatches("u")
    assert result["completed"]==1 and len(fake.calls)==1 and history[0]["state"]=="completed"
    assert fake.calls[0][0][2]==history[0]["run_key"] and fake.calls[0][1]["allow_safe_retry"] is True


@pytest.mark.asyncio
async def test_worker_records_control_block_as_skipped_not_failed(tmp_path):
    repo=ScheduleRepository(tmp_path/"skipped.db");now=datetime.now(timezone.utc);_due(repo,now)
    fake=FakeRunner({"status":"skipped","outcome":"BLOCKED","reason_codes":["demo_autonomous_disabled"],
        "run_id":"r","preview_id":None,"execution_id":None})
    counts=await AutonomousSchedulerWorker(worker_id="skip",schedules=repo,runner_factory=lambda:fake).run_once(now)
    dispatch=repo.list_dispatches("u")[0]
    assert counts["skipped"]==1 and counts["retrying"]==0
    assert dispatch["state"]=="skipped" and dispatch["reason_code"]=="demo_autonomous_disabled"


@pytest.mark.asyncio
async def test_safe_retry_is_bounded_but_blocked_and_unknown_do_not_retry(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"autonomous_scheduler_max_retries",1)
    now=datetime.now(timezone.utc)
    repo=ScheduleRepository(tmp_path/"safe.db");_due(repo,now)
    transient=FakeRunner({"status":"no_trade","outcome":"NO_TRADE","reason_codes":["provider_unavailable"],"run_id":"r","preview_id":None,"execution_id":None})
    await AutonomousSchedulerWorker(worker_id="safe",schedules=repo,runner_factory=lambda:transient).run_once(now)
    assert repo.list_dispatches("u")[0]["state"]=="retry_wait"
    repo2=ScheduleRepository(tmp_path/"blocked.db");_due(repo2,now)
    blocked=FakeRunner({"status":"blocked","outcome":"BLOCKED","reason_codes":["arming_expired"],"run_id":"r","preview_id":None,"execution_id":None})
    await AutonomousSchedulerWorker(worker_id="blocked",schedules=repo2,runner_factory=lambda:blocked).run_once(now)
    assert repo2.list_dispatches("u")[0]["state"]=="completed"
    repo3=ScheduleRepository(tmp_path/"unknown.db");_due(repo3,now)
    unknown=FakeRunner({"status":"trade","outcome":"TRADE","reason_codes":["unknown"],"run_id":"r","preview_id":"p","execution_id":"e"})
    await AutonomousSchedulerWorker(worker_id="unknown",schedules=repo3,runner_factory=lambda:unknown).run_once(now)
    assert repo3.list_dispatches("u")[0]["state"]=="completed" and repo3.list_dispatches("u")[0]["retry_count"]==0


@pytest.mark.asyncio
async def test_rate_limited_schedule_honors_suggested_retry_without_tight_loop(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"autonomous_scheduler_max_retries",2)
    now=datetime.now(timezone.utc);suggested=(now+timedelta(minutes=4)).isoformat()
    repo=ScheduleRepository(tmp_path/"rate-limited.db");_due(repo,now)
    runner=FakeRunner({"status":"blocked","outcome":"BLOCKED",
        "reason_codes":["tradelocker_rate_limit_exhausted"],"run_id":"r",
        "preview_id":None,"execution_id":None,
        "validation":{"retryable":True,"suggested_retry_at":suggested}})
    counts=await AutonomousSchedulerWorker(worker_id="rate",schedules=repo,
        runner_factory=lambda:runner).run_once(now)
    dispatch=repo.list_dispatches("u")[0]
    assert counts["retrying"]==1 and dispatch["state"]=="retry_wait"
    assert datetime.fromisoformat(dispatch["next_retry_at"])>=datetime.fromisoformat(suggested)


def _account(storage,user,environment):
    connection=storage.save_connection(user,base_url=f"https://{environment}.tradelocker.test/backend-api",username="u",password="p",server="s",environment=environment)
    storage.sync_accounts(user,connection.connection_ref,{"accounts":[{"accountId":"1","accNum":"2"}]})
    return storage.list_accounts(user)[0]


def test_live_profile_can_be_scheduled_while_users_remain_isolated(tmp_path):
    db=tmp_path/"app.db";brokers=BrokerRepository(db,"secret");schedules=ScheduleRepository(db)
    live=_account(brokers,"live","live");profile=brokers.create_profile("live",name="live",account_ref=live["public_id"])
    service=AutonomousScheduleService(schedules=schedules,brokers=brokers)
    saved=service.save("live",profile["public_id"])
    assert saved["profile_ref"]==profile["public_id"]
    schedules.upsert_schedule("alice","p1",timezone_name="America/Chicago",local_times=["05:00"],enabled=False,next_run_at=None)
    schedules.upsert_schedule("bob","p2",timezone_name="America/Chicago",local_times=["05:00"],enabled=False,next_run_at=None)
    assert [item["profile_ref"] for item in schedules.list_schedules("alice")]==["p1"]


def test_worker_health_reports_heartbeat_and_stopped_state(tmp_path):
    repo=ScheduleRepository(tmp_path/"scheduler.db");repo.heartbeat("one","running",{"due":0})
    assert repo.worker_health()["status"]=="healthy"
    repo.heartbeat("one","stopped")
    assert repo.worker_health()["status"]=="unavailable"


def test_scheduled_time_rechecks_durable_environment_controls(monkeypatch):
    runner=object.__new__(AutonomousDecisionRunner)
    controls={"global_autonomous_kill_switch":False,"demo_autonomous_enabled":True,"live_autonomous_enabled":False}
    runner.execution=type("Controls",(),{"kill_switch_enabled":lambda self:False,
        "get_autonomous_controls":lambda self,user:controls})()
    now=datetime(2026,1,14,14,tzinfo=timezone.utc)
    profile={"enabled":True,"execution_mode":"demo_autonomous","autonomous_armed":True,
        "armed_until":(now+timedelta(hours=1)).isoformat(),"account_environment":"demo","is_demo":1,
        "allowed_sessions":["new_york"],"decision_provider":"no_trade"}
    monkeypatch.setattr(settings,"kill_switch_enabled",False)
    assert runner._blocking_reasons(profile,now,"user")==[]
    assert runner._blocking_reasons({**profile,"armed_until":(now-timedelta(seconds=1)).isoformat()},now,"user")==[]
    assert runner._blocking_reasons({**profile,"autonomous_armed":False},now,"user")==[]
    assert "profile_disabled" in runner._blocking_reasons({**profile,"enabled":False},now)
    monkeypatch.setattr(settings,"kill_switch_enabled",True)
    controls["global_autonomous_kill_switch"]=True
    assert "global_autonomous_kill_switch_enabled" in runner._blocking_reasons(profile,now,"user")


def test_disabled_schedule_is_never_claimed(tmp_path):
    repo=ScheduleRepository(tmp_path/"scheduler.db");now=datetime.now(timezone.utc)
    repo.upsert_schedule("u","p",timezone_name="America/Chicago",local_times=["05:00"],enabled=False,
        next_run_at=(now-timedelta(minutes=1)).isoformat())
    assert repo.claim_due(worker_id="one",now=now,lease_seconds=30,limit=10,next_run=_next)==[]


def test_daily_summary_reports_outcomes_account_state_and_real_entries(tmp_path):
    db=tmp_path/"app.db";execution=ExecutionRepository(db);now=datetime.now(timezone.utc).isoformat()
    base={"id":"run1","run_key":"summary-key","user_sub":"u","profile_ref":"p","strategy_ref":"s","strategy_version":"1",
        "decision_provider":"fixture","trigger_reason":"test","state":"claimed","started_at":now,"created_at":now,"updated_at":now,
        "context_json":{"account":{"balance":1000,"equity":990},"risk_state":{"daily_realized_pnl":-10,"open_pnl":0}},
        "decision_json":{"action":"NO_TRADE","confidence":0,"reason_codes":["weak"],"rationale":"weak"}}
    execution.claim_decision_run(base);execution.update_decision_run("run1",state="no_trade",reason_codes_json=["weak"],completed_at=now)
    service=AutonomousScheduleService(schedules=ScheduleRepository(db),brokers=BrokerRepository(db,"secret"),execution=execution)
    summary=service.daily_summary("u")
    assert summary["outcomes"]["NO_TRADE"]==1 and summary["daily_entry_count"]==0
    assert summary["runs"][0]["balance"]==1000 and summary["runs"][0]["daily_pnl"]==-10
