from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.models.autonomous import AutonomousOrderProposal, ExecutionMode
from app.services.autonomous.execution import (
    AutonomousDemoService,
    AutonomousExecutionError,
    InstrumentMetadata,
    VerifiedDemoContext,
    calculate_reward_risk,
    calculate_broker_position_size,
    normalize_pair,
    parse_instrument_metadata,
)
from app.brokers.tradelocker.client import TradeLockerError
from app.models.tradelocker import (
    TradeLockerAccountIdentity, TradeLockerAccountStatus, TradeLockerMarginStatus,
    TradeLockerTodayStatus,
)
from app.services.providers.errors import ProviderError
from app.services.tradelocker.account_status import AccountStatusUnavailable
from app.storage.execution import ExecutionRepository
from app.storage.brokers import BrokerConnection, BrokerRepository


def metadata(**overrides):
    values = {
        "instrument_id": "101", "route_id": "trade-route", "contract_size": 100_000,
        "pip_size": 0.0001, "lot_step": 0.01, "min_lots": 0.01,
        "max_lots": 100.0, "quote_currency": "USD", "minimum_stop_distance": 0.0001,
        "commission_per_lot": 0.0,
        "leverage": 100.0,
    }
    values.update(overrides)
    return InstrumentMetadata(**values)


@pytest.mark.parametrize("raw", ["EUR/USD", "EURUSD", "eurusd", "eur_usd"])
def test_pair_normalization(raw):
    assert normalize_pair(raw) == "EURUSD"


def test_usd_quoted_position_sizing_uses_contract_and_increment():
    result = calculate_broker_position_size(
        balance=10_000, available_funds=10_000, risk_percent=1,
        entry=1.1000, stop_loss=1.0990, metadata=metadata(), quote_to_account_rate=1,
    )
    assert result["lot_size"] == pytest.approx(1.0)
    assert result["quantity"] == pytest.approx(100_000)
    assert result["estimated_risk"] == pytest.approx(100)
    assert result["estimated_margin"] == pytest.approx(1100)


def test_sizing_rejects_unverifiable_or_insufficient_margin():
    with pytest.raises(AutonomousExecutionError,match="leverage"):
        calculate_broker_position_size(balance=10_000,available_funds=10_000,risk_percent=.25,
            entry=1.1,stop_loss=1.099,metadata=metadata(leverage=None),quote_to_account_rate=1)
    with pytest.raises(AutonomousExecutionError) as error:
        calculate_broker_position_size(balance=10_000,available_funds=10,risk_percent=.25,
            entry=1.1,stop_loss=1.099,metadata=metadata(),quote_to_account_rate=1)
    assert error.value.code=="insufficient_margin"


def test_usd_base_position_sizing_converts_quote_currency():
    result = calculate_broker_position_size(
        balance=10_000, available_funds=10_000, risk_percent=1,
        entry=1.25, stop_loss=1.249, metadata=metadata(quote_currency="CAD"),
        quote_to_account_rate=0.8,
    )
    assert result["lot_size"] == pytest.approx(1.25)
    assert result["estimated_risk"] == pytest.approx(100)


def test_position_sizing_rounds_down_and_never_up():
    result = calculate_broker_position_size(
        balance=1_000, available_funds=1_000, risk_percent=1,
        entry=1.1, stop_loss=1.0987, metadata=metadata(lot_step=0.01),
        quote_to_account_rate=1,
    )
    assert result["lot_size"] == 0.07
    assert result["estimated_risk"] <= 10


@pytest.mark.parametrize("balance,funds", [(0, 1000), (1000, 0), (-1, 1000)])
def test_position_sizing_rejects_nonpositive_funds(balance, funds):
    with pytest.raises(AutonomousExecutionError) as error:
        calculate_broker_position_size(
            balance=balance, available_funds=funds, risk_percent=1,
            entry=1.1, stop_loss=1.099, metadata=metadata(), quote_to_account_rate=1,
        )
    assert error.value.code == "insufficient_account_funds"


def test_position_sizing_fails_closed_without_conversion():
    with pytest.raises(AutonomousExecutionError) as error:
        calculate_broker_position_size(
            balance=1000, available_funds=1000, risk_percent=1,
            entry=1.1, stop_loss=1.099, metadata=metadata(), quote_to_account_rate=0,
        )
    assert error.value.code == "position_size_unverifiable"


def test_instrument_metadata_requires_verified_broker_values():
    payload = {
        "instrument_id": 42,
        "listing": {"routes": [{"type": "TRADE", "id": "r1"}]},
        "details": {"d": {"contractSize": 100000, "pipSize": 0.0001,
                           "lotStep": 0.01, "minOrderQty": 0.01,
                           "maxOrderQty": 20, "quoteCurrency": "USD",
                           "minStopLossDistance": 0.0002, "commissionPerLot": 7}},
    }
    result = parse_instrument_metadata(payload, "EURUSD")
    assert result.instrument_id == "42"
    assert result.route_id == "r1"
    assert result.minimum_stop_distance == pytest.approx(0.0002)
    assert result.commission_per_lot == pytest.approx(7)


def test_instrument_metadata_fails_closed_when_contract_missing():
    with pytest.raises(AutonomousExecutionError) as error:
        parse_instrument_metadata({"instrument_id": 1, "listing": {}, "details": {}}, "EURUSD")
    assert error.value.code == "position_size_unverifiable"


def test_instrument_metadata_maps_herofx_tiered_tick_schema():
    payload = {
        "instrument_id": 42,
        "listing": {"routes": [{"type": "TRADE", "id": "r1"}]},
        "details": {"d": {
            "lotSize": 100_000, "lotStep": 0.01, "minLot": 0.01, "maxLot": 50,
            "quotingCurrency": "USD", "leverage": "100.00",
            "tickSize": [{"leftRangeLimit": None, "tickSize": 0.00001}],
        }},
    }
    result = parse_instrument_metadata(payload, "EURUSD")
    assert result.contract_size == 100_000
    assert result.min_lots == 0.01 and result.max_lots == 50
    assert result.tick_size == 0.00001 and result.price_precision == 5
    assert result.pip_size == 0.0001
    assert result.minimum_stop_distance == 0.00001


def test_official_quote_abbreviations_are_supported():
    assert AutonomousDemoService._quote({"d": {"bp": 1.1, "ap": 1.1002}}) == (1.1, 1.1002)


def test_reward_risk_uses_broker_rounding_and_exposes_spread_adjustment():
    result = calculate_reward_risk(
        entry=1.14300, stop_loss=1.14100, take_profit=1.14600,
        bid=1.14280, ask=1.14300,
        metadata=metadata(tick_size=0.00001, price_precision=5),
        minimum_reward_risk=1.5, side="long", order_type="limit",
    )
    assert result["entry_price_basis"] == "requested_limit"
    assert result["gross_risk_distance"] == pytest.approx(0.002)
    assert result["gross_reward_distance"] == pytest.approx(0.003)
    assert result["spread_adjustment"] == pytest.approx(0.0002)
    assert result["risk_distance"] == pytest.approx(0.0022)
    assert result["reward_distance"] == pytest.approx(0.0028)
    assert result["reward_risk"] == pytest.approx(0.0028 / 0.0022)
    assert result["comparison_tolerance"] == 0


def test_cross_currency_conversion_uses_direct_or_inverse_snapshot_quote():
    market = {"EURUSD": {"quote": {"d": {"bp": 1.0998, "ap": 1.1002}}}}
    assert AutonomousDemoService._conversion_rate("EUR", "USD", market) == pytest.approx(1.1)
    assert AutonomousDemoService._conversion_rate("USD", "EUR", market) == pytest.approx(1 / 1.1)


def test_execution_mode_is_account_scoped_and_defaults_read_only(tmp_path):
    repository = ExecutionRepository(tmp_path / "execution.db")
    first = repository.get_or_create_settings("user", "conn", "account-1", "100")
    second = repository.get_or_create_settings("user", "conn", "account-2", "200")
    assert first["execution_mode"] == second["execution_mode"] == "read_only"
    repository.set_mode("user", "conn", "account-1", "100", ExecutionMode.DEMO_AUTONOMOUS)
    assert repository.get_or_create_settings("user", "conn", "account-1", "100")["execution_mode"] == "demo_autonomous"
    assert repository.get_or_create_settings("user", "conn", "account-2", "200")["execution_mode"] == "read_only"


def test_equity_high_watermark_never_moves_down(tmp_path):
    repository = ExecutionRepository(tmp_path / "execution.db")
    repository.get_or_create_settings("user", "conn", "account", "100")
    assert repository.observe_equity("user", "conn", "account", "100", 1000) == 1000
    assert repository.observe_equity("user", "conn", "account", "100", 900) == 1000
    assert repository.observe_equity("user", "conn", "account", "100", 1100) == 1100


def test_submission_claim_is_durable_for_preview_and_key(tmp_path):
    repository = ExecutionRepository(tmp_path / "execution.db")
    # Foreign keys require the immutable snapshot and preview records to exist.
    now = "2099-01-01T00:00:00+00:00"
    repository.insert_snapshot({"id": "snap", "user_sub": "u", "connection_id": "c", "account_id": "a", "acc_num": "n", "environment": "demo", "strategy_name": "s", "strategy_version": "1", "normalized_snapshot_json": "{}", "retrieved_at": now, "expires_at": now, "created_at": now})
    repository.insert_preview({"id": "p1", "snapshot_id": "snap", "user_sub": "u", "connection_id": "c", "account_id": "a", "acc_num": "n", "environment": "demo", "pair": "EURUSD", "instrument_id": "1", "route_id": "r", "side": "long", "order_type": "market", "entry": 1.1, "stop_loss": 1.09, "take_profit": 1.12, "quantity": 1000, "lot_size": .01, "estimated_risk": 10, "risk_percent": 1, "estimated_reward": 20, "reward_risk": 2, "broker_metadata_json": "{}", "status": "approved", "violations_json": "[]", "expires_at": now, "created_at": now})
    claimed, _ = repository.claim_submission("s1", "p1", "key-12345", "fingerprint")
    duplicate, existing = repository.claim_submission("s2", "p1", "other-key", "fingerprint")
    assert claimed is True
    assert duplicate is False
    assert existing["id"] == "s1"


def test_concurrent_submission_claims_have_one_winner(tmp_path):
    repository = ExecutionRepository(tmp_path / "execution.db")
    now = "2099-01-01T00:00:00+00:00"
    repository.insert_snapshot({"id": "snap", "user_sub": "u", "connection_id": "c", "account_id": "a", "acc_num": "n", "environment": "demo", "strategy_name": "s", "strategy_version": "1", "normalized_snapshot_json": "{}", "retrieved_at": now, "expires_at": now, "created_at": now})
    repository.insert_preview({"id": "p1", "snapshot_id": "snap", "user_sub": "u", "connection_id": "c", "account_id": "a", "acc_num": "n", "environment": "demo", "pair": "EURUSD", "instrument_id": "1", "route_id": "r", "side": "long", "order_type": "market", "entry": 1.1, "stop_loss": 1.09, "take_profit": 1.12, "quantity": 1000, "lot_size": .01, "estimated_risk": 10, "risk_percent": 1, "estimated_reward": 20, "reward_risk": 2, "broker_metadata_json": "{}", "status": "approved", "violations_json": "[]", "expires_at": now, "created_at": now})

    def claim(index):
        return repository.claim_submission(f"submission-{index}", "p1", "same-key", "same-fingerprint")[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_database_constraints_reject_duplicate_idempotency_key(tmp_path):
    repository = ExecutionRepository(tmp_path / "execution.db")
    with repository._connect() as db:
        indexes = db.execute("PRAGMA index_list(broker_submissions)").fetchall()
    assert sum(bool(row["unique"]) for row in indexes) >= 3


class DiscoveryClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def get_accounts(self):
        return {"accounts": [{"accountId": "a1", "accNum": "7", "name": "Anything", "currency": "USD"}]}

    async def aclose(self):
        pass


def configured_service(tmp_path, monkeypatch, *, base_url="https://demo.tradelocker.test/backend-api", environment="demo"):
    monkeypatch.setattr(settings, "tradelocker_demo_base_url", "https://demo.tradelocker.test/backend-api")
    brokers = BrokerRepository(tmp_path / "broker.db", secret="test-secret")
    brokers.save_connection("user", base_url=base_url, username="u", password="p", server="s", environment=environment)
    brokers.select_account("user", "a1", "7")
    account = brokers.list_accounts("user")[0]
    profile = brokers.create_profile("user", name="Demo profile", account_ref=account["public_id"])
    execution = ExecutionRepository(tmp_path / "broker.db")
    service = AutonomousDemoService(
        broker_repository=brokers, execution_repository=execution, client_factory=DiscoveryClient,
    )
    service.test_profile_ref = profile["public_id"]
    return service, execution


@pytest.mark.asyncio
async def test_legacy_read_only_profile_is_implicitly_manual(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path, monkeypatch)
    context = await service.context("user", service.test_profile_ref)
    assert context.execution_mode == ExecutionMode.DEMO_MANUAL


@pytest.mark.asyncio
async def test_demo_mode_can_be_enabled_only_for_scoped_account(tmp_path, monkeypatch):
    service, execution = configured_service(tmp_path, monkeypatch)
    service.brokers.update_profile("user", service.test_profile_ref, execution_mode="demo_manual")
    context = await service.context("user", service.test_profile_ref)
    assert context.execution_mode == ExecutionMode.DEMO_MANUAL
    assert context.account_id == "a1" and context.acc_num == "7"


@pytest.mark.asyncio
async def test_required_macro_policy_is_loaded_from_bound_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tradelocker_demo_base_url", "https://demo.tradelocker.test/backend-api")
    brokers = BrokerRepository(tmp_path / "broker.db", secret="test-secret")
    connection = brokers.save_connection("user", base_url=settings.tradelocker_demo_base_url,
        username="u", password="p", server="s", environment="demo")
    brokers.sync_accounts("user", connection.connection_ref,
        {"accounts": [{"accountId": "a1", "accNum": "7", "currency": "USD"}]})
    account = brokers.list_accounts("user")[0]
    profile = brokers.create_profile("user", name="AI demo", account_ref=account["public_id"],
        strategy_template_id="strategy_ai_forex_confluence_v1", execution_mode="demo_manual")
    service = AutonomousDemoService(broker_repository=brokers,
        execution_repository=ExecutionRepository(tmp_path / "broker.db"), client_factory=DiscoveryClient)

    context = await service.context("user", profile["public_id"])

    assert context.risk["strategy_config"]["required_macro_series"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url,environment",
    [
        ("https://live.tradelocker.test/backend-api", "demo"),
        ("https://demo.tradelocker.test/backend-api", "live"),
        ("https://live.tradelocker.test/backend-api", "live"),
    ],
)
async def test_environment_conflicts_fail_closed(tmp_path, monkeypatch, base_url, environment):
    service, _ = configured_service(tmp_path, monkeypatch, base_url=base_url, environment=environment)
    with pytest.raises(AutonomousExecutionError) as error:
        await service.context("user", service.test_profile_ref, require_mode=False)
    assert error.value.code == "demo_environment_verification_failed"


class SnapshotAccountService:
    async def retrieve(self, user_sub, account_alias):
        return TradeLockerAccountStatus(
            retrieved_at=datetime.now(timezone.utc),
            account=TradeLockerAccountIdentity(account_id="a1", account_number="7",
                account_alias=account_alias, name="Demo", currency="USD",
                environment="demo", active=True),
            balance=10_000, projected_balance=10_000, available_funds=9_000,
            blocked_balance=0, cash_balance=10_000, withdrawal_available=9_000,
            open_gross_pnl=0, open_net_pnl=0, positions_count=0, pending_orders_count=0,
            today=TradeLockerTodayStatus(gross=0, net=0, fees=0, volume=0, trades_count=0),
            margin=TradeLockerMarginStatus(initial_requirement=0, maintenance_requirement=0,
                warning_level=100, stop_out_level=50, warning_requirement=0,
                margin_before_warning=9_000),
        )


class MissingSnapshotAccountService:
    async def retrieve(self, user_sub, account_alias):
        raise AccountStatusUnavailable("account_state_unavailable", "unavailable")


class UnavailableFinnhub:
    async def economic_calendar(self, *args):
        raise ProviderError("finnhub", "upstream_failure", "unavailable")
    async def market_news(self, *args):
        raise AssertionError("calendar failure should short-circuit news")
    async def aclose(self): pass


class UnavailableFred:
    async def release_dates(self, *args):
        raise ProviderError("fred", "upstream_failure", "unavailable")
    async def aclose(self): pass


class SnapshotClient:
    def __init__(self, *, fail_component=None, calls=None, **kwargs):
        self.fail_component = fail_component
        self.calls = calls if calls is not None else []
        self.place_order_calls = 0

    def _fail(self, component):
        if self.fail_component == component:
            raise TradeLockerError(component, "sanitized failure")

    async def get_accounts(self):
        return {"accounts": [{"accountId": "a1", "accNum": "7", "name": "Demo", "currency": "USD"}]}

    async def get_config(self):
        self._fail("trade_config")
        return {"d": {
            "positionsConfig": {"columns": [{"id": "id"}, {"id": "qty"}, {"id": "openPnl"}]},
            "ordersConfig": {"columns": [{"id": "id"}, {"id": "qty"}, {"id": "price"}]},
            "ordersHistoryConfig": {"columns": [{"id": "id"}]},
        }}

    async def get_open_positions(self):
        self.calls.append("get_open_positions")
        self._fail("positions")
        return {"d": [["position-1", 0]] if self.fail_component == "positions_mapping"
            else [["position-1", 0, 0]]}

    async def get_orders(self):
        self._fail("pending_orders")
        return {"d": [["bad-row"]] if self.fail_component == "pending_orders_mapping" else []}

    async def get_orders_history(self):
        self._fail("order_history")
        return {"d": []}

    async def get_quote(self, symbol):
        self._fail("quote")
        return {"d": {"bp": 1.14280, "ap": 1.14300}}

    async def get_instrument_details(self, symbol):
        self._fail("instrument_metadata")
        return {"instrument_id": "42", "listing": {"routes": [{"type": "TRADE", "id": "route-1"}]},
            "details": {"d": {"contractSize": 100_000, "pipSize": 0.0001,
                "tickSize": 0.00001, "pricePrecision": 5, "lotStep": 0.01,
                "minOrderQty": 0.01, "maxOrderQty": 10, "quoteCurrency": "USD",
                "minStopLossDistance": 0.0001, "commissionPerLot": 0, "leverage": 100}}}

    async def get_candles(self, symbol, timeframe, count, **kwargs):
        self.calls.append(f"candles:{symbol}:{timeframe}:{count}")
        self._fail(f"candles_{timeframe}")
        return SimpleNamespace(complete=True, candles=[])

    async def place_order(self, order):
        self.place_order_calls += 1
        raise AssertionError("tests must not submit an order")

    async def aclose(self): pass


def snapshot_service(tmp_path, monkeypatch, *, fail_component=None, mode="demo_manual",
                     missing_account=False):
    monkeypatch.setattr(settings, "tradelocker_demo_base_url", "https://demo.tradelocker.test/backend-api")
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    brokers = BrokerRepository(tmp_path / "snapshot.db", "secret")
    connection = brokers.save_connection("user", base_url=settings.tradelocker_demo_base_url,
        username="u", password="p", server="HeroFX", environment="demo")
    brokers.sync_accounts("user", connection.connection_ref,
        {"accounts": [{"accountId": "a1", "accNum": "7", "currency": "USD"}]})
    account = brokers.list_accounts("user")[0]
    profile = brokers.create_profile("user", name="Manual", account_ref=account["public_id"],
        execution_mode="demo_manual")
    if mode == "demo_autonomous":
        profile = brokers.arm_autonomous_profile("user", profile["public_id"],
            armed_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            shadow_mode=True)
    calls = []
    clients = []
    def factory(**kwargs):
        client = SnapshotClient(fail_component=fail_component, calls=calls, **kwargs)
        clients.append(client)
        return client
    service = AutonomousDemoService(broker_repository=brokers,
        execution_repository=ExecutionRepository(tmp_path / "snapshot.db"),
        client_factory=factory, account_status_service=MissingSnapshotAccountService() if missing_account else SnapshotAccountService(),
        finnhub_factory=UnavailableFinnhub, fred_factory=UnavailableFred)
    return service, profile["public_id"], calls, clients


@pytest.mark.asyncio
async def test_manual_snapshot_maps_required_broker_components_and_uses_positions_method(tmp_path, monkeypatch):
    service, profile, calls, clients = snapshot_service(tmp_path, monkeypatch)
    result = await service.snapshot("user", profile, "EURUSD")
    assert result["status"] == "ok"
    assert result["positions"] == [{"id": "position-1", "qty": 0, "openPnl": 0}]
    assert result["pending_orders"] == []
    assert result["market"]["pairs"]["EURUSD"]["bid"] == pytest.approx(1.1428)
    assert result["market"]["pairs"]["EURUSD"]["spread"] == pytest.approx(0.0002)
    assert result["market"]["pairs"]["EURUSD"]["instrument_metadata"]["tick_size"] == 0.00001
    assert "get_open_positions" in calls
    assert "provider_unavailable" in result["warnings"]
    assert all(client.place_order_calls == 0 for client in clients)


@pytest.mark.asyncio
async def test_global_autonomous_kill_switch_does_not_block_manual_demo_snapshot(tmp_path, monkeypatch):
    service, profile, _, clients = snapshot_service(tmp_path, monkeypatch)
    service.execution.update_autonomous_controls("user", {
        "global_autonomous_kill_switch": True,
    }, updated_by="test", source="test")

    result = await service.snapshot("user", profile, "EURUSD", autonomous=False)

    assert result["status"] == "ok"
    assert all(client.place_order_calls == 0 for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "missing"), [
    ("trade_config", "trade_config"), ("positions", "positions"),
    ("positions_mapping", "positions_mapping"), ("pending_orders", "pending_orders"),
    ("pending_orders_mapping", "pending_orders_mapping"), ("quote", "eurusd_quote"),
    ("instrument_metadata", "eurusd_instrument_metadata"),
])
async def test_snapshot_failure_names_exact_missing_component(tmp_path, monkeypatch, failure, missing):
    service, profile, _, _ = snapshot_service(tmp_path, monkeypatch, fail_component=failure)
    with pytest.raises(AutonomousExecutionError) as error:
        await service.snapshot("user", profile, "EURUSD")
    assert error.value.code == "market_snapshot_unavailable"
    assert error.value.reasons == [f"{missing}_unavailable"]
    assert error.value.as_dict()["missing_component"] == missing


@pytest.mark.asyncio
async def test_snapshot_failure_names_account_status_component(tmp_path, monkeypatch):
    service, profile, _, _ = snapshot_service(tmp_path, monkeypatch, missing_account=True)
    with pytest.raises(AutonomousExecutionError) as error:
        await service.snapshot("user", profile, "EURUSD")
    assert error.value.as_dict()["missing_component"] == "account_status"
    assert error.value.reasons == ["account_status_unavailable"]


@pytest.mark.asyncio
async def test_autonomous_snapshot_succeeds_when_required_daily_candles_are_available(
    tmp_path, monkeypatch
):
    service, profile, calls, clients = snapshot_service(
        tmp_path, monkeypatch, mode="demo_autonomous"
    )
    result = await service.snapshot("user", profile, "EURUSD", autonomous=True)
    assert result["status"] == "ok"
    assert "candles:EURUSD:1d:190" in calls
    assert result["market"]["pairs"]["EURUSD"]["complete"] is True
    assert set(result["market"]["pairs"]["EURUSD"]["timeframes"]) == {"1d", "4h", "1h", "15m"}
    assert all(client.place_order_calls == 0 for client in clients)


@pytest.mark.asyncio
async def test_autonomous_daily_candle_failure_exposes_request_diagnostics(tmp_path, monkeypatch):
    service, profile, _, clients = snapshot_service(
        tmp_path, monkeypatch, fail_component="candles_1d", mode="demo_autonomous"
    )
    with pytest.raises(AutonomousExecutionError) as error:
        await service.snapshot("user", profile, "EURUSD", autonomous=True)
    payload = error.value.as_dict()
    assert payload["missing_component"] == "candle_history"
    assert payload["failing_timeframes"] == ["1d"]
    daily = payload["timeframes"]["1d"]
    assert daily["provider_timeframe"] == "1D"
    assert daily["metadata"]["rows_received"] == 0
    assert daily["metadata"]["mapping_failure"] is None
    assert "eurusd_candles_1d_provider_request_failed" in payload["blocking_reasons"]
    assert all(client.place_order_calls == 0 for client in clients)


@pytest.mark.asyncio
async def test_manual_preview_warns_on_finnhub_and_exposes_broker_calculation(tmp_path, monkeypatch):
    service, profile, _, clients = snapshot_service(tmp_path, monkeypatch)
    snapshot = await service.snapshot("user", profile, "EURUSD")
    proposal = AutonomousOrderProposal(
        snapshot_id=snapshot["snapshot_id"], pair="EURUSD", side="long", order_type="limit",
        entry=1.14300, stop_loss=1.14100, take_profit=1.14700,
    )
    preview = await service.review("user", profile, proposal)
    assert preview["status"] == "approved" and preview["preview_id"]
    assert "provider_unavailable" in preview["warnings"]
    assert preview["calculation"]["spread_adjustment"] == pytest.approx(0.0002)
    assert preview["calculation"]["risk_distance"] == pytest.approx(0.0022)
    assert preview["calculation"]["reward_distance"] == pytest.approx(0.0038)
    assert preview["reward_risk"] > 1.5
    assert all(client.place_order_calls == 0 for client in clients)


@pytest.mark.asyncio
async def test_nominal_one_point_five_is_blocked_by_disclosed_spread_adjustment(tmp_path, monkeypatch):
    service, profile, _, _ = snapshot_service(tmp_path, monkeypatch)
    snapshot = await service.snapshot("user", profile, "EURUSD")
    proposal = AutonomousOrderProposal(
        snapshot_id=snapshot["snapshot_id"], pair="EURUSD", side="long", order_type="limit",
        entry=1.14300, stop_loss=1.14100, take_profit=1.14600,
    )
    with pytest.raises(AutonomousExecutionError) as error:
        await service.review("user", profile, proposal)
    assert error.value.reasons == ["reward_risk_too_low"]
    calculation = error.value.details["calculation"]
    assert calculation["gross_reward_distance"] / calculation["gross_risk_distance"] == pytest.approx(1.5)
    assert calculation["reward_risk"] == pytest.approx(0.0028 / 0.0022)


@pytest.mark.asyncio
async def test_autonomous_preview_still_fails_closed_on_finnhub(tmp_path, monkeypatch):
    service, profile, _, clients = snapshot_service(tmp_path, monkeypatch, mode="demo_autonomous")
    snapshot = await service.snapshot("user", profile, "EURUSD", autonomous=True)
    proposal = AutonomousOrderProposal(
        snapshot_id=snapshot["snapshot_id"], pair="EURUSD", side="long", order_type="limit",
        entry=1.14300, stop_loss=1.14100, take_profit=1.14700,
    )
    with pytest.raises(AutonomousExecutionError) as error:
        await service.review("user", profile, proposal, autonomous=True)
    assert "provider_unavailable" in error.value.reasons
    assert all(client.place_order_calls == 0 for client in clients)


def reconciliation_payloads(*, quantity=0.01, side="buy", stop=1.09, target=1.12):
    config = {"d": {
        "ordersConfig": {"columns": [{"id": "id"}]},
        "ordersHistoryConfig": {"columns": [{"id": key} for key in (
            "id", "tradableInstrumentId", "qty", "side", "positionId", "stopLoss", "takeProfit"
        )]},
        "positionsConfig": {"columns": [{"id": "id"}, {"id": "tradableInstrumentId"}]},
    }}
    orders = {"d": {"orders": []}}
    history = {"d": {"ordersHistory": [["o1", "42", quantity, side, "pos1", stop, target]]}}
    positions = {"d": {"positions": [["pos1", "42"]]}}
    preview = {"instrument_id": "42", "lot_size": 0.01, "side": "long", "stop_loss": 1.09, "take_profit": 1.12}
    return config, orders, history, positions, preview


def test_reconciliation_maps_positional_order_and_position_fields():
    config, orders, history, positions, preview = reconciliation_payloads()
    result = AutonomousDemoService._reconcile(
        config, orders, history, positions, preview, {"d": {"orderId": "o1"}}
    )
    assert result["verified"] is True
    assert result["order_found"] is True and result["position_found"] is True
    assert result["broker_position_id"] == "pos1"


@pytest.mark.parametrize(
    "overrides,failed_check",
    [({"quantity": 0.02}, "quantity_matches"), ({"side": "sell"}, "side_matches"),
     ({"stop": 1.08}, "stop_loss_matches"), ({"target": 1.13}, "take_profit_matches")],
)
def test_reconciliation_reports_protection_and_order_mismatches(overrides, failed_check):
    config, orders, history, positions, preview = reconciliation_payloads(**overrides)
    result = AutonomousDemoService._reconcile(
        config, orders, history, positions, preview, {"d": {"orderId": "o1"}}
    )
    assert result["verified"] is False
    assert result[failed_check] is False


class SubmissionClient:
    def __init__(self, *, visible_after_order_read=2):
        self.visible_after_order_read = visible_after_order_read
        self.order_reads = 0
        self.place_order_calls = 0
        self.submitted = False
        self.strategy_id = None

    @staticmethod
    def config():
        order_columns = [
            "id", "tradableInstrumentId", "qty", "side", "type", "status", "price",
            "stopLoss", "takeProfit", "isOpen", "strategyId", "accountId", "accNum",
        ]
        return {"d": {
            "ordersConfig": {"columns": [{"id": key} for key in order_columns]},
            "ordersHistoryConfig": {"columns": [{"id": key} for key in order_columns]},
            "positionsConfig": {"columns": [{"id": key} for key in (
                "id", "tradableInstrumentId", "qty", "side", "stopLoss", "takeProfit",
                "strategyId", "accountId", "accNum",
            )]},
        }}

    def order_row(self):
        return [
            "432345564236299554", "4665", "1.19", "buy", "limit", "New", "1.143",
            "1.141", "1.147", "true", self.strategy_id, "account-a", "1001",
        ]

    async def get_config(self): return self.config()

    async def get_orders(self):
        self.order_reads += 1
        visible = self.submitted and self.order_reads >= self.visible_after_order_read
        return {"d": [self.order_row()] if visible else []}

    async def get_orders_history(self): return {"d": []}
    async def get_open_positions(self): return {"d": []}
    async def get_quote(self, symbol): return {"d": {"bp": 1.1429, "ap": 1.143}}

    async def place_order(self, payload):
        self.place_order_calls += 1
        self.submitted = True
        self.strategy_id = payload["strategyId"]
        return {}

    async def aclose(self): pass


def submission_preview():
    return {
        "id": "preview_c17435280a1e4d12b380b9f2de482813",
        "account_id": "account-a", "acc_num": "1001", "pair": "EURUSD",
        "instrument_id": "4665", "side": "long", "order_type": "limit",
        "entry": 1.143, "stop_loss": 1.141, "take_profit": 1.147,
        "lot_size": 1.19, "quantity": 119000,
        "broker_metadata": metadata(instrument_id="4665", route_id="route-1",
            tick_size=0.00001, price_precision=5).__dict__,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_after,expected_attempts", [(1, 1), (3, 3)])
async def test_bounded_reconciliation_polls_until_pending_new_order_is_visible(
    monkeypatch, visible_after, expected_attempts,
):
    monkeypatch.setattr(settings, "autonomous_broker_verification_max_attempts", 4)
    monkeypatch.setattr(settings, "autonomous_broker_verification_initial_delay_seconds", 0)
    client = SubmissionClient(visible_after_order_read=visible_after)
    client.submitted = True
    client.strategy_id = "afd-4d12b380b9f2de482813"
    service = AutonomousDemoService()

    result = await service._poll_reconciliation(
        client, client.config(), submission_preview(), {}, client.strategy_id,
    )

    assert result["verified"] is True
    assert result["verification_attempts"] == expected_attempts
    assert result["matching_method"] == "correlation_id"
    assert result["broker_status"] == "New" and result["broker_status_accepted"] is True
    assert result["broker_order_id"] == "432345564236299554"
    assert result["side"] == "buy" and result["symbol"] == "EURUSD"
    assert result["quantity_lots"] == pytest.approx(1.19)
    assert result["quantity_units"] == pytest.approx(119000)


@pytest.mark.asyncio
async def test_reconciliation_retries_a_transient_post_submit_read_error(monkeypatch):
    class FlakyReadClient(SubmissionClient):
        async def get_orders(self):
            self.order_reads += 1
            if self.order_reads == 1:
                raise TradeLockerError("get_orders", "sanitized", code="http_error", status_code=502)
            return {"d": [self.order_row()]}

    monkeypatch.setattr(settings, "autonomous_broker_verification_max_attempts", 3)
    monkeypatch.setattr(settings, "autonomous_broker_verification_initial_delay_seconds", 0)
    client = FlakyReadClient()
    client.submitted = True
    client.strategy_id = "afd-4d12b380b9f2de482813"

    result = await AutonomousDemoService()._poll_reconciliation(
        client, client.config(), submission_preview(), {}, client.strategy_id,
    )

    assert result["verified"] is True and result["verification_attempts"] == 2


def test_reconciliation_uses_full_composite_only_when_durable_ids_are_absent():
    client = SubmissionClient()
    client.strategy_id = None
    result = AutonomousDemoService._reconcile(
        client.config(), {"d": [client.order_row()]}, {"d": []}, {"d": []},
        submission_preview(), {}, correlation_id="different-correlation",
    )
    assert result["verified"] is True
    assert result["matching_method"] == "composite"


def test_reconciliation_keeps_lots_units_and_account_boundaries_distinct():
    client = SubmissionClient()
    preview = submission_preview()
    client.strategy_id = "afd-4d12b380b9f2de482813"
    wrong_quantity = client.order_row()
    wrong_quantity[2] = "119000"
    result = AutonomousDemoService._reconcile(
        client.config(), {"d": [wrong_quantity]}, {"d": []}, {"d": []}, preview, {},
        correlation_id=client.strategy_id,
    )
    assert result["quantity_matches"] is False and result["verified"] is False

    wrong_account = client.order_row()
    wrong_account[11] = "account-b"
    result = AutonomousDemoService._reconcile(
        client.config(), {"d": [wrong_account]}, {"d": []}, {"d": []}, preview, {},
        correlation_id=client.strategy_id,
    )
    assert result["account_matches"] is False and result["verified"] is False


def submission_service(tmp_path, monkeypatch, client, *, origin="autonomous"):
    monkeypatch.setattr(settings, "autonomous_broker_verification_initial_delay_seconds", 0)
    monkeypatch.setattr(settings, "autonomous_broker_verification_max_attempts", 3)
    repository = ExecutionRepository(tmp_path / "submission.db")
    repository.get_or_create_settings("user", "connection-a", "account-a", "1001")
    repository.update_autonomous_controls("user", {
        "global_autonomous_kill_switch": False, "demo_autonomous_enabled": True,
    }, updated_by="test", source="test")
    now = datetime.now(timezone.utc)
    repository.insert_snapshot({
        "id": "snap-submit", "user_sub": "user", "connection_id": "connection-a",
        "account_id": "account-a", "acc_num": "1001", "environment": "demo",
        "strategy_name": "strategy", "strategy_version": "1", "normalized_snapshot_json": "{}",
        "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "created_at": now.isoformat(),
    })
    preview = submission_preview()
    repository.insert_preview({
        **{key: value for key, value in preview.items() if key != "broker_metadata"},
        "snapshot_id": "snap-submit", "user_sub": "user",
        "connection_id": "connection-a", "environment": "demo", "profile_ref": "profile-a",
        "account_record_id": "account-record-a", "connection_ref": "connection-ref-a",
        "account_alias": "herofx-demo-1", "server": "HeroFX",
        "base_url": "https://demo.tradelocker.test/backend-api", "demo_classification": "demo",
        "route_id": "route-1", "estimated_risk": 100, "risk_percent": 1,
        "estimated_reward": 200, "reward_risk": 2,
        "broker_metadata_json": json.dumps(preview["broker_metadata"]),
        "status": "approved", "violations_json": "[]", "execution_origin": origin,
        "expires_at": (now + timedelta(minutes=5)).isoformat(), "created_at": now.isoformat(),
    })
    connection = BrokerConnection(
        connection_id="connection-a", connection_ref="connection-ref-a",
        base_url="https://demo.tradelocker.test/backend-api", username="u", password="p",
        server="HeroFX", account_id="account-a", account_number="1001", environment="demo",
    )
    context = VerifiedDemoContext(
        user_sub="user", connection_id="connection-a", account_id="account-a", acc_num="1001",
        account_name="Demo", currency="USD", environment="demo", server="HeroFX",
        base_url=connection.base_url, execution_mode=ExecutionMode.DEMO_AUTONOMOUS,
        risk={"strategy_name": "strategy", "strategy_version": "1", "strategy_config": {},
              "maximum_open_positions": 1, "maximum_pending_orders": 1,
              "maximum_new_entries_per_day": 2, "daily_loss_limit_percent": 3,
              "drawdown_cutoff_percent": 10, "risk_per_trade_percent": 3},
        connection=connection, profile_ref="profile-a", account_record_id="account-record-a",
        account_alias="herofx-demo-1", connection_ref="connection-ref-a",
        connection_label="HeroFX Demo", demo_classification="demo",
    )
    seen_client_args = []
    def factory(**kwargs):
        seen_client_args.append(kwargs)
        return client
    service = AutonomousDemoService(
        execution_repository=repository, client_factory=factory,
        account_status_service=SnapshotAccountService(),
    )
    async def bound_context(*args, **kwargs): return context
    async def providers():
        return ({"finnhub": {"available": True, "stale": False},
                 "fred": {"available": True, "stale": False}}, [], {})
    service.context = bound_context
    service._provider_state = providers
    service._kill_switch = lambda *_: False
    return service, repository, preview["id"], seen_client_args


@pytest.mark.asyncio
async def test_autonomous_submission_uses_repaired_account_bound_verification(tmp_path, monkeypatch):
    client = SubmissionClient(visible_after_order_read=2)
    service, _, preview_id, seen = submission_service(tmp_path, monkeypatch, client)

    result = await service.submit("user", preview_id, "autonomous:durable-key")

    assert result["status"] == "submitted" and result["manual_review_required"] is False
    assert result["broker_order_id"] == "432345564236299554"
    assert result["broker_status"] == "New"
    assert result["quantity_lots"] == pytest.approx(1.19)
    assert result["quantity_units"] == pytest.approx(119000)
    assert client.place_order_calls == 1
    assert seen[0]["account_id"] == "account-a" and seen[0]["account_number"] == "1001"


@pytest.mark.asyncio
async def test_global_autonomous_kill_switch_blocks_autonomous_submission_before_broker_write(tmp_path, monkeypatch):
    client = SubmissionClient(visible_after_order_read=2)
    service, repository, preview_id, _ = submission_service(tmp_path, monkeypatch, client)
    repository.update_autonomous_controls("user", {
        "global_autonomous_kill_switch": True,
    }, updated_by="test", source="test")

    with pytest.raises(AutonomousExecutionError) as error:
        await service.submit("user", preview_id, "autonomous:blocked-key")

    assert error.value.code == "global_autonomous_kill_switch_enabled"
    assert client.place_order_calls == 0


@pytest.mark.asyncio
async def test_unknown_consumes_key_and_later_read_only_lookup_reconciles(tmp_path, monkeypatch):
    client = SubmissionClient(visible_after_order_read=999)
    service, repository, preview_id, _ = submission_service(tmp_path, monkeypatch, client, origin="manual")
    monkeypatch.setattr(settings, "autonomous_broker_verification_max_attempts", 2)

    unknown = await service.submit("user", preview_id, "manual:durable-key")
    duplicate = await service.submit("user", preview_id, "manual:durable-key")

    assert unknown["status"] == duplicate["status"] == "unknown"
    assert unknown["manual_review_required"] is True and unknown["execution_id"]
    assert client.place_order_calls == 1
    submission = repository.get_submission(idempotency_key="manual:durable-key")
    assert submission["submission_state"] == "unknown"

    client.visible_after_order_read = client.order_reads + 1
    reconciled = await service.execution_result("user", unknown["execution_id"])

    assert reconciled["status"] == "submitted"
    assert reconciled["broker_order_id"] == "432345564236299554"
    assert client.place_order_calls == 1
    assert repository.get_submission(idempotency_key="manual:durable-key")["submission_state"] == "verified"
