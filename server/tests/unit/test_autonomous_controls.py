from datetime import datetime, timezone

from app.config.settings import settings
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.execution import ExecutionRepository
from app.storage.brokers import BrokerRepository
from app.storage.schedules import ScheduleRepository
import asyncio


def test_controls_default_safe_are_durable_audited_and_user_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    database = tmp_path / "controls.db"
    first = ExecutionRepository(database)

    defaults = first.get_autonomous_controls("user-a")
    assert defaults["demo_autonomous_enabled"] is False
    assert defaults["live_autonomous_enabled"] is False

    changed = first.update_autonomous_controls(
        "user-a", {"demo_autonomous_enabled": True},
        updated_by="user-a", source="test", reason="enable demo",
    )
    assert changed["demo_autonomous_enabled"] is True
    assert ExecutionRepository(database).get_autonomous_controls("user-a")["demo_autonomous_enabled"] is True
    assert ExecutionRepository(database).get_autonomous_controls("user-b")["demo_autonomous_enabled"] is False
    audit = first.autonomous_control_audit("user-a")
    assert audit[0]["control_name"] == "demo_autonomous_enabled"
    assert audit[0]["old_value"] is False and audit[0]["new_value"] is True
    assert first.autonomous_control_audit("user-b") == []


def test_environment_controls_replace_timed_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    repository = ExecutionRepository(tmp_path / "controls.db")
    runner = object.__new__(AutonomousDecisionRunner)
    runner.execution = repository
    profile = {
        "enabled": True, "account_available": True, "broker_active": True,
        "locally_enabled": True, "account_environment": "demo", "is_demo": 1,
        "allowed_sessions": [], "decision_provider": "no_trade",
        "autonomous_armed": False, "armed_until": "2000-01-01T00:00:00+00:00",
    }
    now = datetime(2026, 1, 14, 14, tzinfo=timezone.utc)

    assert "demo_autonomous_disabled" in runner._blocking_reasons(profile, now, "user")
    repository.update_autonomous_controls("user", {"demo_autonomous_enabled": True}, updated_by="user", source="test")
    assert runner._blocking_reasons(profile, now, "user") == []
    repository.update_autonomous_controls("user", {"global_autonomous_kill_switch": True}, updated_by="user", source="test")
    assert "global_autonomous_kill_switch_enabled" in runner._blocking_reasons(profile, now, "user")


def test_live_toggle_fails_closed_without_live_execution_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    repository = ExecutionRepository(tmp_path / "controls.db")
    repository.update_autonomous_controls("user", {"live_autonomous_enabled": True}, updated_by="user", source="test")
    runner = object.__new__(AutonomousDecisionRunner); runner.execution = repository
    profile = {"enabled": True, "account_available": True, "broker_active": True, "locally_enabled": True,
        "account_environment": "live", "is_demo": 0, "allowed_sessions": [], "decision_provider": "no_trade"}
    reasons = runner._blocking_reasons(profile, datetime(2026, 1, 14, 14, tzinfo=timezone.utc), "user")
    assert reasons == ["live_autonomous_execution_not_implemented"]


def test_missing_openai_configuration_blocks_before_any_broker_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    database = tmp_path / "provider-block.db"
    brokers, profile = _profile_for_environment(database, "demo")
    brokers.update_profile("demo", profile["public_id"], decision_provider="openai",
        model_identifier=None, minimum_confidence=0.7)
    execution = ExecutionRepository(database)
    execution.update_autonomous_controls("demo", {"demo_autonomous_enabled": True},
        updated_by="demo", source="test")

    class NoBrokerCalls:
        calls = 0
        async def snapshot(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Broker snapshot must not run when provider configuration is blocked.")

    demo = NoBrokerCalls()
    runner = AutonomousDecisionRunner(brokers=brokers, execution=execution, demo=demo)
    result = asyncio.run(runner.run("demo", profile["public_id"], "missing-provider-config", "test"))

    assert result["status"] == "skipped"
    assert "openai_api_key_missing" in result["reason_codes"]
    assert "model_not_selected" in result["reason_codes"]
    assert demo.calls == 0


def _profile_for_environment(database, environment):
    brokers = BrokerRepository(database, "secret")
    connection = brokers.save_connection(environment, base_url=f"https://{environment}.tradelocker.test/backend-api",
        username="u", password="p", server=environment, environment=environment)
    brokers.sync_accounts(environment, connection.connection_ref,
        {"accounts": [{"accountId": f"{environment}-account", "accNum": "1"}]})
    account = brokers.list_accounts(environment)[0]
    profile = brokers.create_profile(environment, name=f"{environment}-profile", account_ref=account["public_id"])
    return brokers, profile


def test_demo_and_live_disabled_runs_are_recorded_as_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    database = tmp_path / "runner.db"
    for environment, reason in (("demo", "demo_autonomous_disabled"), ("live", "live_autonomous_disabled")):
        brokers, profile = _profile_for_environment(database, environment)
        execution = ExecutionRepository(database)
        runner = AutonomousDecisionRunner(brokers=brokers, execution=execution)
        result = __import__("asyncio").run(runner.run(environment, profile["public_id"], f"run-key-{environment}", "test"))
        assert result["status"] == "skipped"
        assert reason in result["reason_codes"]
        stored = execution.get_decision_run(environment, result["run_id"])
        assert stored["state"] == "skipped"


def test_profile_deletion_can_detach_schedule_and_preserves_execution_history(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    database = tmp_path / "delete.db"
    brokers, profile = _profile_for_environment(database, "demo")
    schedules = ScheduleRepository(database)
    schedules.upsert_schedule("demo", profile["public_id"], timezone_name="UTC", local_times=["12:00"],
        enabled=True, next_run_at="2099-01-01T12:00:00+00:00")
    execution = ExecutionRepository(database)
    now = datetime.now(timezone.utc).isoformat()
    execution.insert_run({"id":"historical-run","user_sub":"demo","connection_id":"c","account_id":"a",
        "acc_num":"1","snapshot_id":None,"preview_id":None,"strategy_name":"s","strategy_version":"1",
        "decision":"no_trade","result_status":"no_trade","result_json":{},"started_at":now,
        "completed_at":now,"created_at":now})

    assert schedules.disable_profile_schedule("demo", profile["public_id"])
    assert brokers.delete_profile("demo", profile["public_id"])

    assert schedules.list_schedules("demo")[0]["enabled"] is False
    assert execution.get_run("demo", "historical-run") is not None
