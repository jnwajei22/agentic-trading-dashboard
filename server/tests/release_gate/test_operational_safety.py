from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import settings
from app.services.autonomous.decision import DeterministicTestDecisionProvider, DecisionAction, StructuredDecision
from app.services.autonomous import runner as runner_module
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.execution import utcnow
from scripts.autonomous_demo_readiness import _connect_readonly
from scripts.disable_demo_kill_switch import CONFIRMATION, disable


def test_kill_switch_is_durable_and_visible_across_process_repositories(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    database = tmp_path / "release-gate.db"
    api_process = ExecutionRepository(database)
    worker_process = ExecutionRepository(database)

    assert api_process.kill_switch_enabled() is False
    api_process.enable_kill_switch("release-operator")

    assert api_process.kill_switch_enabled() is True
    assert worker_process.kill_switch_enabled() is True


def test_readiness_database_connection_cannot_write(tmp_path):
    database = tmp_path / "release-gate.db"
    ExecutionRepository(database)

    with _connect_readonly(database) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO operational_controls(key,value,updated_at) VALUES('probe','x','now')"
            )


def test_only_explicit_local_recovery_can_disable_durable_kill_switch(tmp_path):
    database = tmp_path / "release-gate.db"
    repository = ExecutionRepository(database)
    repository.enable_kill_switch("release-operator")
    with pytest.raises(ValueError):
        disable(database, "wrong", "release-operator")
    assert repository.kill_switch_enabled() is True

    disable(database, CONFIRMATION, "release-operator")

    assert ExecutionRepository(database).kill_switch_enabled() is False


class _ManualExecutionFixture:
    def __init__(self):
        self.review_calls = 0
        self.submit_calls = 0

    async def snapshot(self, user_sub, profile_ref, *, autonomous):
        candles = [{"timestamp": i, "open": 1.1, "high": 1.101, "low": 1.099,
            "close": 1.1 + i / 1_000_000, "volume": 1} for i in range(60)]
        pair = {"bid": 1.1, "ask": 1.1001, "spread": .0001, "complete": True,
            **{f"candles_{timeframe}": candles for timeframe in ("1d", "4h", "1h", "15m")}}
        return {"snapshot_id": "release-snapshot", "retrieved_at": utcnow().isoformat(),
            "expires_at": (utcnow() + timedelta(minutes=1)).isoformat(), "account_ref": "account-safe",
            "connection_ref": "connection-safe", "account": {"account": {"currency": "USD"},
            "balance": 10_000, "projected_balance": 10_000, "available_funds": 9_000},
            "risk_state": {"daily_realized_pnl": 0, "open_pnl": 0}, "positions": [],
            "pending_orders": [], "recent_order_history": [], "news_blackouts": [],
            "providers": {"finnhub": {"available": True}, "fred": {"available": True}},
            "provider_context": {}, "market": {"pairs": {"EURUSD": pair}}, "execution_eligibility": True}

    async def review(self, user_sub, profile_ref, proposal, *, autonomous):
        self.review_calls += 1
        assert autonomous is True and proposal.snapshot_id == "release-snapshot"
        return {"preview_id": "release-preview"}

    async def submit(self, user_sub, preview_id, idempotency_key):
        self.submit_calls += 1
        assert preview_id == "release-preview" and idempotency_key.startswith("autonomous:")
        return {"status": "verified", "execution_id": "release-execution"}


@pytest.mark.asyncio
async def test_autonomous_trade_uses_manual_preview_submit_service_once(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-only")
    gate_time = datetime(2026, 7, 16, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(runner_module, "utcnow", lambda: gate_time)
    database = tmp_path / "release-gate.db"
    brokers = BrokerRepository(database, "release-secret")
    connection = brokers.save_connection("user", base_url="https://demo.tradelocker.test/backend-api",
        username="u", password="p", server="s", environment="demo")
    brokers.sync_accounts("user", connection.connection_ref,
        {"accounts": [{"accountId": "a1", "accNum": "7", "currency": "USD"}]})
    account = brokers.list_accounts("user")[0]
    profile = brokers.create_profile("user", name="Release AI", account_ref=account["public_id"],
        strategy_template_id="strategy_ai_forex_confluence_v1")
    brokers.arm_autonomous_profile("user", profile["public_id"],
        armed_until=(utcnow() + timedelta(hours=1)).isoformat(), decision_provider="openai",
        model_identifier="fixture-model",
        allowed_sessions=["london", "new_york", "overlap"], shadow_mode=False)
    service = _ManualExecutionFixture()
    provider = DeterministicTestDecisionProvider(StructuredDecision(action=DecisionAction.TRADE,
        symbol="EURUSD", side="long", order_type="market", entry=1.1001, stop_loss=1.099,
        take_profit=1.1023, confidence=.9, reason_codes=["release_fixture"], rationale="fixture"))
    runner = AutonomousDecisionRunner(brokers=brokers, execution=ExecutionRepository(database),
        demo=service, provider=provider)

    first = await runner.run("user", profile["public_id"], "release-run-key", "release gate")
    duplicate = await runner.run("user", profile["public_id"], "release-run-key", "release gate")

    assert first["status"] == "trade" and first["execution_id"] == "release-execution"
    assert duplicate["duplicate"] is True
    assert service.review_calls == service.submit_calls == 1
