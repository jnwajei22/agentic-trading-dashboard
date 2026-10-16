from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import settings
from app.services.autonomous.control import AutonomyControlService
from app.services.autonomous.decision import DecisionAction, DeterministicTestDecisionProvider, StructuredDecision
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository, utcnow
from app.storage.schedules import ScheduleRepository


class SafeRunner:
    def __init__(self, status: str = "no_trade", reasons: list[str] | None = None):
        self.status = status
        self.reasons = reasons or []
        self.keys: list[str] = []

    async def run(self, user, profile, run_key, trigger_reason, **kwargs):
        duplicate = run_key in self.keys
        if not duplicate: self.keys.append(run_key)
        return {"run_id": "safe-run", "status": self.status, "outcome": "BLOCKED" if self.reasons else "NO_TRADE",
            "reason_codes": self.reasons, "duplicate": duplicate, "preview_id": None, "execution_id": None}


def configured(monkeypatch):
    monkeypatch.setattr(settings, "autonomous_decision_provider", "openai")
    monkeypatch.setattr(settings, "autonomous_decision_model", "server-test-model")
    monkeypatch.setattr(settings, "openai_api_key", "server-test-key")
    monkeypatch.setattr(settings, "kill_switch_enabled", True)
    monkeypatch.setattr(settings, "autonomous_scheduler_required_for_readiness", False)


def setup_service(tmp_path, monkeypatch, *, runner=None):
    configured(monkeypatch)
    database = tmp_path / "chatgpt-control.db"
    brokers = BrokerRepository(database, "test-secret")
    connection = brokers.save_connection("user", base_url="https://demo.tradelocker.test/backend-api",
        username="private", password="private", server="HeroFX", environment="demo", label="HeroFX")
    brokers.sync_accounts("user", connection.connection_ref,
        {"accounts": [{"accountId": "demo-1", "accNum": "7", "currency": "USD"}]})
    account = brokers.list_accounts("user")[0]
    brokers.rename_account("user", account["public_id"], "herofx-demo-1")
    brokers.set_default_account("user", account["public_id"])
    account = brokers.list_accounts("user")[0]
    profile = brokers.create_profile("user", name="AI Demo Competition", account_ref=account["public_id"],
        strategy_template_id="strategy_hourly_forex_v1")
    execution = ExecutionRepository(database)
    schedules = ScheduleRepository(database)
    schedules.heartbeat("test-worker", "running", {"test_fixture": True})
    safe = runner or SafeRunner()
    service = AutonomyControlService(brokers=brokers, execution=execution, schedules=schedules,
        runner_factory=lambda: safe)
    return service, brokers, execution, schedules, account, profile, safe


@pytest.mark.asyncio
async def test_one_demo_start_resolves_selected_account_profile_and_persists_complete_state(tmp_path, monkeypatch):
    service, brokers, execution, schedules, account, profile, runner = setup_service(tmp_path, monkeypatch)
    execution.update_autonomous_controls("user", {"live_autonomous_enabled": True},
        updated_by="test", source="test")

    result = await service.start("user", "demo")

    assert result["status"] == "started"
    assert result["account_alias"] == "herofx-demo-1" and result["profile_ref"] == profile["public_id"]
    assert result["autonomous_enabled"] is True and result["global_kill_switch"] is False
    controls = execution.get_autonomous_controls("user")
    assert controls["demo_autonomous_enabled"] is True and controls["live_autonomous_enabled"] is True
    assert result["schedule"]["created"] is True and result["schedule"]["enabled"] is True
    stored_schedule = schedules.get_profile_schedule("user", profile["public_id"])
    assert stored_schedule["timezone"] == "UTC"
    assert stored_schedule["expression"]["times"] == [f"{hour:02d}:00" for hour in range(7, 21)]
    stored_profile = next(item for item in brokers.list_profiles("user") if item["public_id"] == profile["public_id"])
    assert stored_profile["decision_provider"] == "openai"
    assert stored_profile["model_identifier"] == "server-test-model"
    assert result["immediate_run"]["run_id"] == "safe-run" and len(runner.keys) == 1
    audit = execution.autonomous_control_audit("user")
    assert {item["control_name"] for item in audit} >= {"demo_autonomous_enabled", "global_autonomous_kill_switch"}
    status = service.status("user")
    assert status["demo_autonomous_enabled"] is True and status["decision_engine"]["ready"] is True
    assert status["worker_health"]["status"] == "healthy"
    assert status["selected_accounts"] == [{"account_alias": "herofx-demo-1", "environment": "demo"}]
    assert status["schedules"][0]["next_run_at"] is not None


@pytest.mark.asyncio
async def test_start_reuses_enabled_or_disabled_schedule_and_repeated_calls_are_idempotent(tmp_path, monkeypatch):
    service, _, _, schedules, _, profile, runner = setup_service(tmp_path, monkeypatch)
    original = service.schedule_service.save("user", profile["public_id"], timezone_name="UTC",
        local_times=["09:00"], enabled=False)

    first = await service.start("user", "demo")
    second = await service.start("user", "demo")

    assert first["schedule"]["schedule_id"] == original["id"]
    assert first["schedule"]["created"] is False and first["schedule"]["enabled"] is True
    assert second["schedule"]["schedule_id"] == original["id"]
    assert len(schedules.list_schedules("user")) == 1
    assert len(set(runner.keys)) == 1
    assert second["immediate_run"]["duplicate"] is True


@pytest.mark.asyncio
async def test_blocked_immediate_run_does_not_disable_started_autonomy(tmp_path, monkeypatch):
    blocked = SafeRunner("blocked", ["maximum_pending_orders_reached"])
    service, _, execution, _, _, _, _ = setup_service(tmp_path, monkeypatch, runner=blocked)

    result = await service.start("user", "demo")

    assert result["status"] == "started_with_blocked_run"
    assert result["immediate_run"]["blocking_reasons"] == ["maximum_pending_orders_reached"]
    assert execution.get_autonomous_controls("user")["demo_autonomous_enabled"] is True


@pytest.mark.asyncio
async def test_missing_server_decision_configuration_blocks_without_mutation(tmp_path, monkeypatch):
    service, _, execution, schedules, _, _, runner = setup_service(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", None)

    with pytest.raises(Exception) as raised:
        await service.start("user", "demo")

    error = raised.value.as_dict()
    assert error["error"] == "decision_engine_not_configured"
    assert error["missing_settings"] == ["OPENAI_API_KEY"]
    assert execution.get_autonomous_controls("user")["demo_autonomous_enabled"] is False
    assert schedules.list_schedules("user") == [] and runner.keys == []


@pytest.mark.asyncio
async def test_unhealthy_persistent_worker_blocks_scheduled_start(tmp_path, monkeypatch):
    service, _, execution, schedules, _, _, runner = setup_service(tmp_path, monkeypatch)
    schedules.heartbeat("test-worker", "stopped")
    with pytest.raises(Exception) as raised:
        await service.start("user", "demo")
    assert raised.value.code == "autonomous_worker_unavailable"
    assert execution.get_autonomous_controls("user")["demo_autonomous_enabled"] is False
    assert runner.keys == []


@pytest.mark.asyncio
async def test_multiple_profiles_and_environment_fallback_fail_closed(tmp_path, monkeypatch):
    service, brokers, _, _, account, _, _ = setup_service(tmp_path, monkeypatch)
    brokers.create_profile("user", name="Second", account_ref=account["public_id"])
    with pytest.raises(Exception) as ambiguous:
        await service.start("user", "demo", run_now=False)
    assert ambiguous.value.code == "multiple_enabled_profiles"
    with pytest.raises(Exception) as mismatch:
        await service.start("user", "live", live_confirmation="ENABLE LIVE AUTONOMY", run_now=False)
    assert mismatch.value.code == "live_autonomous_execution_not_implemented"


@pytest.mark.asyncio
async def test_live_start_requires_confirmation_and_fails_before_enabling_unsupported_live(tmp_path, monkeypatch):
    service, _, execution, _, _, _, _ = setup_service(tmp_path, monkeypatch)
    with pytest.raises(Exception) as confirmation:
        await service.start("user", "live")
    assert confirmation.value.code == "live_confirmation_required"
    with pytest.raises(Exception) as unsupported:
        await service.start("user", "live", live_confirmation="ENABLE LIVE AUTONOMY")
    assert unsupported.value.code == "live_autonomous_execution_not_implemented"
    assert execution.get_autonomous_controls("user")["live_autonomous_enabled"] is False


@pytest.mark.asyncio
async def test_account_fallback_never_crosses_environments(tmp_path, monkeypatch):
    service, brokers, _, _, _, _, _ = setup_service(tmp_path, monkeypatch)
    live = brokers.save_connection("user", base_url="https://live.tradelocker.test/backend-api",
        username="live", password="private", server="HeroFX", environment="live", create_new=True)
    brokers.sync_accounts("user", live.connection_ref,
        {"accounts": [{"accountId": "live-1", "accNum": "8"}]})
    live_account = next(item for item in brokers.list_accounts("user") if item["environment"] == "live")
    brokers.set_default_account("user", live_account["public_id"])
    with pytest.raises(Exception) as mismatch:
        await service.start("user", "demo", run_now=False)
    assert mismatch.value.code == "account_environment_mismatch"


def test_stop_and_emergency_stop_preserve_schedules_orders_and_other_environment(tmp_path, monkeypatch):
    service, _, execution, schedules, _, profile, runner = setup_service(tmp_path, monkeypatch)
    execution.update_autonomous_controls("user", {"demo_autonomous_enabled": True,
        "live_autonomous_enabled": True, "global_autonomous_kill_switch": False}, updated_by="test", source="test")
    service.schedule_service.save("user", profile["public_id"], timezone_name="UTC", local_times=["09:00"])

    stopped = service.stop("user", "demo")
    assert stopped["demo_autonomous_enabled"] is False and stopped["live_autonomous_enabled"] is True
    assert stopped["orders_and_positions_preserved"] is True and len(schedules.list_schedules("user")) == 1
    emergency = service.emergency_stop("user", "Operator requested emergency stop")
    assert emergency["global_kill_switch"] is True and emergency["orders_and_positions_preserved"] is True
    assert any(item["reason"] == "Operator requested emergency stop"
        for item in execution.autonomous_control_audit("user"))
    assert runner.keys == []


class PendingLimitDemo:
    def __init__(self): self.review_calls = 0; self.submit_calls = 0
    async def snapshot(self, user_sub, profile_ref, *, autonomous):
        candles = [{"timestamp": i, "open": 1.1, "high": 1.101, "low": 1.099,
            "close": 1.1 + i / 1_000_000, "volume": 1} for i in range(60)]
        pair = {"bid": 1.1, "ask": 1.1001, "spread": .0001, "complete": True,
            **{f"candles_{timeframe}": candles for timeframe in ("1d", "4h", "1h", "15m")}}
        return {"snapshot_id": "pending-limit", "retrieved_at": utcnow().isoformat(),
            "expires_at": (utcnow() + timedelta(minutes=1)).isoformat(), "account_ref": "safe",
            "connection_ref": "safe", "account": {"account": {"currency": "USD"}, "balance": 10_000,
            "projected_balance": 10_000, "available_funds": 9_000},
            "risk_state": {"daily_realized_pnl": 0, "open_pnl": 0,
                "blocking_reasons": ["maximum_pending_orders_reached"]},
            "positions": [], "pending_orders": [{"status": "working"}], "recent_order_history": [],
            "news_blackouts": [], "providers": {}, "provider_context": {},
            "market": {"pairs": {"EURUSD": pair}}, "execution_eligibility": False}
    async def review(self, *args, **kwargs): self.review_calls += 1; raise AssertionError("review must not run")
    async def submit(self, *args, **kwargs): self.submit_calls += 1; raise AssertionError("submit must not run")


@pytest.mark.asyncio
async def test_real_runner_records_pending_limit_without_preview_or_broker_write(tmp_path, monkeypatch):
    weekday = datetime(2026, 7, 15, 14, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.autonomous.runner.utcnow", lambda: weekday)
    service, brokers, execution, _, _, profile, _ = setup_service(tmp_path, monkeypatch)
    brokers.update_profile("user", profile["public_id"], decision_provider="openai",
        model_identifier="server-test-model", minimum_confidence=.7)
    execution.update_autonomous_controls("user", {"demo_autonomous_enabled": True,
        "global_autonomous_kill_switch": False}, updated_by="test", source="test")
    demo = PendingLimitDemo()
    provider = DeterministicTestDecisionProvider(StructuredDecision(action=DecisionAction.TRADE,
        symbol="EURUSD", side="long", order_type="market", entry=1.1001, stop_loss=1.099,
        take_profit=1.1023, confidence=.9, reason_codes=["fixture"], rationale="fixture"))
    runner = AutonomousDecisionRunner(brokers=brokers, execution=execution, demo=demo, provider=provider)

    result = await runner.run("user", profile["public_id"], "pending-limit-key", "test")

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["maximum_pending_orders_reached"]
    assert result["preview_id"] is None and result["execution_id"] is None
    assert demo.review_calls == demo.submit_calls == 0


@pytest.mark.asyncio
async def test_dry_run_never_reviews_or_submits_even_when_trade_candidate_passes(tmp_path, monkeypatch):
    weekday = datetime(2026, 7, 15, 14, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.autonomous.runner.utcnow", lambda: weekday)
    _, brokers, execution, _, _, profile, _ = setup_service(tmp_path, monkeypatch)
    brokers.update_profile("user", profile["public_id"], decision_provider="openai",
        model_identifier="server-test-model", minimum_confidence=.7, enabled=False)
    execution.update_autonomous_controls("user", {"demo_autonomous_enabled": False,
        "global_autonomous_kill_switch": True}, updated_by="test", source="test")

    class SafeDryRunDemo(PendingLimitDemo):
        async def snapshot(self, user_sub, profile_ref, *, autonomous):
            assert autonomous is False
            result = await super().snapshot(user_sub, profile_ref, autonomous=autonomous)
            result["risk_state"]["blocking_reasons"] = []
            result["pending_orders"] = []
            result["execution_eligibility"] = True
            return result

    demo = SafeDryRunDemo()
    provider = DeterministicTestDecisionProvider(StructuredDecision(action=DecisionAction.TRADE,
        symbol="EURUSD", side="long", order_type="market", entry=1.1001, stop_loss=1.099,
        take_profit=1.1023, confidence=.9, reason_codes=["fixture"], rationale="fixture"))
    runner = AutonomousDecisionRunner(brokers=brokers, execution=execution, demo=demo, provider=provider)

    result = await runner.run("user", profile["public_id"], "dry-run-trade-candidate", "demo_test", dry_run=True)

    assert result["status"] == "no_trade"
    assert result["reason_codes"] == ["dry_run_trade_candidate"]
    assert result["dry_run"] is True and result["execution_id"] is None
    assert demo.review_calls == demo.submit_calls == 0
