from __future__ import annotations

import logging
import pytest

from app.auth.identity import reset_current_claims, set_current_claims
from app.config.settings import settings
from app.services import desk_tools as tools
from app.services.autonomous.execution import AutonomousExecutionError
from app.storage.brokers import BrokerRepository


class AccountBoundRowsClient:
    positions = {
        "hero-account": [["hero-position", 0, 0]],
        "other-account": [["other-position", 2, 4.5]],
    }
    orders = {
        "hero-account": [["hero-order", 0, 1.11]],
        "other-account": [["other-order", 3, 1.22]],
    }
    calls: list[tuple[str, str, str]] = []

    def __init__(self, **kwargs):
        self.account_id = kwargs["account_id"]
        self.account_number = kwargs["account_number"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_config(self):
        return {"d": {
            "positionsConfig": {"columns": [{"id": "id"}, {"id": "qty"}, {"id": "openPnl"}]},
            "ordersConfig": {"columns": [{"id": "id"}, {"id": "qty"}, {"id": "price"}]},
        }}

    async def get_open_positions(self):
        self.calls.append(("positions", self.account_id, self.account_number))
        return {"d": self.positions[self.account_id]}

    async def get_orders(self):
        self.calls.append(("orders", self.account_id, self.account_number))
        return {"d": self.orders[self.account_id]}


def configured_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "rows.db"))
    monkeypatch.setattr(settings, "broker_secret_key", "row-test-secret")
    repository = BrokerRepository()
    hero = repository.save_connection("user", base_url="https://demo.tradelocker.test/backend-api",
        username="hero-private", password="hero-password", server="HeroFX", environment="demo")
    repository.sync_accounts("user", hero.connection_ref,
        {"accounts": [{"accountId": "hero-account", "accNum": "7", "currency": "USD"}]})
    other = repository.save_connection("user", base_url="https://demo.tradelocker.test/backend-api",
        username="other-private", password="other-password", server="Other", environment="demo",
        create_new=True)
    repository.sync_accounts("user", other.connection_ref,
        {"accounts": [{"accountId": "other-account", "accNum": "8", "currency": "USD"}]})
    accounts = repository.list_accounts("user")
    hero_account = next(item for item in accounts if item["connection_id"] == hero.connection_ref)
    other_account = next(item for item in accounts if item["connection_id"] == other.connection_ref)
    repository.rename_account("user", hero_account["public_id"], "hero-demo")
    repository.rename_account("user", other_account["public_id"], "other-demo")
    AccountBoundRowsClient.calls.clear()
    monkeypatch.setattr(tools, "TradeLockerClient", AccountBoundRowsClient)


@pytest.mark.asyncio
async def test_open_positions_are_mapped_and_preserve_zero_values_per_account(tmp_path, monkeypatch):
    configured_accounts(tmp_path, monkeypatch)
    token = set_current_claims({"sub": "user"})
    try:
        result = await tools.get_open_positions("hero-demo")
    finally:
        reset_current_claims(token)

    assert result["status"] == "ok"
    assert result["positions"] == [{"id": "hero-position", "qty": 0, "openPnl": 0}]
    assert all(row["id"] != "other-position" for row in result["positions"])
    assert AccountBoundRowsClient.calls == [("positions", "hero-account", "7")]


@pytest.mark.asyncio
async def test_zero_positions_returns_an_empty_normalized_list(tmp_path, monkeypatch):
    configured_accounts(tmp_path, monkeypatch)
    monkeypatch.setitem(AccountBoundRowsClient.positions, "hero-account", [])
    token = set_current_claims({"sub": "user"})
    try:
        result = await tools.get_open_positions("hero-demo")
    finally:
        reset_current_claims(token)

    assert result == {"status": "ok", "account": result["account"], "positions": []}


@pytest.mark.asyncio
async def test_pending_orders_remain_mapped_and_account_isolated(tmp_path, monkeypatch):
    configured_accounts(tmp_path, monkeypatch)
    token = set_current_claims({"sub": "user"})
    try:
        hero = await tools.get_pending_orders("hero-demo")
        other = await tools.get_pending_orders("other-demo")
    finally:
        reset_current_claims(token)

    assert hero["orders"] == [{"id": "hero-order", "qty": 0, "price": 1.11}]
    assert other["orders"] == [{"id": "other-order", "qty": 3, "price": 1.22}]
    assert AccountBoundRowsClient.calls == [
        ("orders", "hero-account", "7"), ("orders", "other-account", "8")]


@pytest.mark.asyncio
async def test_valid_demo_review_has_complete_control_payload(monkeypatch):
    class ReviewService:
        async def snapshot(self, user, profile, symbol):
            return {"snapshot_id": "snapshot-safe", "market": {"pairs": {"EURUSD": {
                "quote": {"d": {"bp": 1.1000, "ap": 1.1002}}}}}}

        @staticmethod
        def _quote(payload):
            return 1.1000, 1.1002

        async def review(self, user, profile, proposal):
            assert user == "user" and profile == "profile-safe"
            assert proposal.entry == 1.1002
            return {"status": "approved", "preview_id": "preview-safe",
                "expires_at": "2099-01-01T00:00:00+00:00", "quantity": 1_000,
                "lot_size": .01, "estimated_risk": 2.5, "risk_percent": .25,
                "estimated_margin": 11.0, "violations": []}

    monkeypatch.setattr(tools, "AutonomousDemoService", ReviewService)
    token = set_current_claims({"sub": "user"})
    try:
        result = await tools.review_demo_order("profile-safe", "EURUSD", "long", "market",
            1.0992, 1.1022, "bounded test setup")
    finally:
        reset_current_claims(token)

    assert result["preview_id"] == "preview-safe"
    assert result["expires_at"]
    assert result["quantity"] == 1_000
    assert result["estimated_risk"] == 2.5
    assert result["risk_percent"] == .25
    assert result["estimated_margin"] == 11.0
    assert result["submission_allowed"] is True
    assert result["blocking_reasons"] == []


@pytest.mark.asyncio
async def test_failed_review_dependency_returns_controlled_payload(monkeypatch, caplog):
    class FailedReviewService:
        async def snapshot(self, user, profile, symbol):
            raise AutonomousExecutionError("market_snapshot_unavailable",
                "dependency-secret-must-not-leak")

    monkeypatch.setattr(tools, "AutonomousDemoService", FailedReviewService)
    token = set_current_claims({"sub": "user"})
    try:
        with caplog.at_level(logging.WARNING):
            result = await tools.review_demo_order("profile-safe", "EURUSD", "long", "market",
                1.0992, 1.1022, "bounded test setup")
    finally:
        reset_current_claims(token)

    assert result["status"] == "blocked"
    assert result["error"] == "market_snapshot_unavailable"
    assert result["error_category"] == "market_snapshot_unavailable"
    assert result["blocking_reasons"] == [{"code":"market_snapshot_unavailable",
        "message":"TradeLocker positions, orders, quote, candles, or configuration are unavailable."}]
    assert result["preview_id"] is None
    assert result["submission_allowed"] is False
    assert result["calculation"] == {"requested_symbol":"EURUSD","order_type":"market",
        "quantity":None,"estimated_risk":None,"estimated_margin":None}
    assert "dependency-secret-must-not-leak" not in str(result)
    assert "dependency-secret-must-not-leak" not in caplog.text


@pytest.mark.asyncio
async def test_risk_validation_failure_exposes_exact_safe_blockers(monkeypatch, caplog):
    class RiskRejectedService:
        async def snapshot(self, user, profile, symbol):
            return {"snapshot_id": "snapshot-safe", "market": {"pairs": {"EURUSD": {
                "quote": {"d": {"bp": 1.1000, "ap": 1.1002}}}}}}

        @staticmethod
        def _quote(payload):
            return 1.1000, 1.1002

        async def review(self, user, profile, proposal):
            raise AutonomousExecutionError(
                "risk_validation_failed",
                "internal validation context must not be returned",
                status="rejected",
                reasons=["provider_unavailable", "reward_risk_too_low"],
                details={"calculation": {"requested_symbol": "EURUSD",
                    "risk_distance": 0.0022, "reward_distance": 0.0028,
                    "spread_adjustment": 0.0002, "reward_risk": 1.272727},
                    "warnings": ["provider_unavailable"]},
            )

    monkeypatch.setattr(tools, "AutonomousDemoService", RiskRejectedService)
    token = set_current_claims({"sub": "user"})
    try:
        with caplog.at_level(logging.WARNING):
            result = await tools.review_demo_order(
                "profile-safe", "EURUSD", "long", "market",
                1.0992, 1.1022, "bounded test setup",
            )
    finally:
        reset_current_claims(token)

    assert result["status"] == "blocked"
    assert result["error_category"] == "risk_validation_failed"
    assert result["blocking_reasons"] == [
        {"code": "provider_unavailable", "message":
            "Required Finnhub market-event data is unavailable or stale."},
        {"code": "reward_risk_too_low", "message":
            "The server-calculated reward-to-risk ratio is below the profile minimum."},
    ]
    assert result["preview_id"] is None
    assert result["submission_allowed"] is False
    assert result["calculation"]["requested_symbol"] == "EURUSD"
    assert result["calculation"]["spread_adjustment"] == 0.0002
    assert result["calculation"]["reward_risk"] == 1.272727
    assert result["warnings"] == ["provider_unavailable"]
    assert "blocking_codes=provider_unavailable,reward_risk_too_low" in caplog.text
    assert "internal validation context" not in str(result)
    assert "internal validation context" not in caplog.text


@pytest.mark.asyncio
async def test_snapshot_tool_returns_exact_missing_component(monkeypatch):
    class MissingInstrumentService:
        async def snapshot(self, user, profile, symbol):
            raise AutonomousExecutionError(
                "market_snapshot_unavailable",
                "The required TradeLocker instrument metadata component is unavailable or unmappable.",
                reasons=["eurusd_instrument_metadata_unavailable"],
                details={"missing_component": "eurusd_instrument_metadata"},
            )

    monkeypatch.setattr(tools, "AutonomousDemoService", MissingInstrumentService)
    token = set_current_claims({"sub": "user"})
    try:
        result = await tools.get_demo_trading_snapshot("profile-safe", "EURUSD")
    finally:
        reset_current_claims(token)

    assert result["error"] == "market_snapshot_unavailable"
    assert result["missing_component"] == "eurusd_instrument_metadata"
    assert result["blocking_reasons"] == ["eurusd_instrument_metadata_unavailable"]
