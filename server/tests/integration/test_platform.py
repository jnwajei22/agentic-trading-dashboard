import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.api.routes import platform
from app.auth.identity import reset_current_claims, set_current_claims
from app.brokers.tradelocker import adapter
from app.brokers.tradelocker.client import TradeLockerError
from app.config.settings import settings
from app.main import app
from app.services import desk_tools as tools
from app.storage.brokers import BrokerRepository
from app.services.tradelocker.config_cache import (
    TradeLockerConfigCacheKey,
    tradelocker_config_cache,
)
from app.models.tradelocker import (
    TradeLockerAccountIdentity,
    TradeLockerAccountStatus,
    TradeLockerMarginStatus,
    TradeLockerTodayStatus,
)
from app.services.autonomous.execution import AutonomousDemoService
from app.storage.execution import ExecutionRepository


@pytest.fixture
def platform_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(settings, "sqlite_path", str(db_path))
    monkeypatch.setattr(settings, "broker_secret_key", "unit-test-broker-secret")
    monkeypatch.setattr(settings, "allow_env_broker_fallback", False)
    return BrokerRepository()


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _claims(subject: str) -> dict:
    return {"sub": subject, "email": f"{subject.replace('|', '-')}@example.test"}


def _client_for(subject: str) -> TestClient:
    app.dependency_overrides[platform.current_claims] = lambda: _claims(subject)
    return TestClient(app)


def test_order_calculation_is_owned_read_only_and_never_submits(platform_storage,monkeypatch):
    user="auth0|calculator";connection=platform_storage.save_connection(user,base_url="https://demo.example/backend-api",
        username="user",password="password",server="HeroFX",environment="demo")
    platform_storage.sync_accounts(user,connection.connection_ref,
        {"accounts":[{"accountId":"external-1","accNum":"123","currency":"USD"}]})
    account=platform_storage.list_accounts(user)[0]
    class Client:
        submitted=False
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get_symbols(self):return [{"tradableInstrumentId":"1","symbol":"GBPUSD","assetClass":"forex",
            "tickSize":.00001,"contractSize":100000,"minQty":.01,"qtyStep":.01,"marginRate":.02}]
        async def get_quote(self,symbol):return {"d":{"bp":1.3452,"ap":1.34528}}
        async def get_account_state_payload(self):return {"d":{"balance":100000}}
        async def submit_order(self,*args,**kwargs):Client.submitted=True;raise AssertionError("calculation cannot submit")
        _instrument_rows=staticmethod(lambda payload:payload)
    monkeypatch.setattr(platform,"TradeLockerClient",Client)
    request={"account_id":account["public_id"],"instrument_id":"forex:GBP/USD","side":"buy","order_type":"market",
        "quantity":{"value":.5,"unit":"lot"},"stop_loss":{"mode":"pips","value":40},
        "take_profit":{"mode":"reward_multiple","value":2}}
    with _client_for(user) as client:
        response=client.post("/api/trading/order-calculations",json=request)
    assert response.status_code==200
    result=response.json();assert result["entry_side"]=="Ask" and result["pip_value"]["value"]==5
    assert result["stop_loss"]["distance_pips"]==40 and result["take_profit"]["reward_to_risk"]==2
    assert Client.submitted is False and "preview_id" not in result

    with _client_for("auth0|other-user") as client:
        forbidden=client.post("/api/trading/order-calculations",json=request)
    assert forbidden.status_code==404


def test_order_calculation_rejects_unknown_account_without_broker_access(platform_storage,monkeypatch):
    class ForbiddenClient:
        def __init__(self,**kwargs):raise AssertionError("broker must not be called")
    monkeypatch.setattr(platform,"TradeLockerClient",ForbiddenClient)
    with _client_for("auth0|nobody") as client:
        response=client.post("/api/trading/order-calculations",json={"account_id":"unknown","instrument_id":"forex:EUR/USD",
            "side":"sell","quantity":{"value":1,"unit":"lot"}})
    assert response.status_code==404


def test_generic_provider_routes_project_legacy_tradelocker_records(platform_storage):
    connection = platform_storage.save_connection("auth0|generic", base_url="https://demo.example/backend-api",
        username="user", password="password", server="HeroFX", environment="demo")
    platform_storage.sync_accounts("auth0|generic", connection.connection_ref,
        {"accounts":[{"accountId":"external-1","accNum":"12345678","currency":"USD"}]})
    account = platform_storage.list_accounts("auth0|generic")[0]
    with _client_for("auth0|generic") as client:
        providers = client.get("/api/providers").json()["providers"]
        assert next(item for item in providers if item["provider_type"]=="tradingview_chart")["execution_provider"] is False
        generic_connection = client.get("/api/trading/connections").json()["connections"][0]
        generic_account = client.get("/api/trading/accounts").json()["accounts"][0]
        legacy_connection = client.get("/api/broker/connections").json()["connections"][0]
        legacy_account = client.get("/api/broker/accounts").json()["accounts"][0]
        assert generic_connection["public_id"] == legacy_connection["public_id"]
        assert generic_connection["broker_name"] != generic_connection["platform_name"]
        assert generic_account["public_id"] == legacy_account["public_id"] == account["public_id"]
        assert generic_account["capabilities"]["order_submission"] is True
        public_payload = str({"providers":providers,"connection":generic_connection,"account":generic_account}).lower()
        assert "password" not in public_payload and "api_key" not in public_payload and "ciphertext" not in public_payload
        assert client.put(f"/api/trading/accounts/{account['public_id']}/primary").status_code == 200


def test_autonomous_controls_api_is_durable_audited_and_requires_live_confirmation(platform_storage, monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    with _client_for("auth0|controls-a") as client:
        initial = client.get("/api/autonomous-controls")
        assert initial.status_code == 200
        assert initial.json()["live_autonomous_enabled"] is False
        changed = client.patch("/api/autonomous-controls", json={"demo_autonomous_enabled": True, "reason": "dashboard"})
        assert changed.status_code == 200 and changed.json()["demo_autonomous_enabled"] is True
        rejected = client.patch("/api/autonomous-controls", json={"live_autonomous_enabled": True})
        assert rejected.status_code == 409
        accepted = client.patch("/api/autonomous-controls", json={
            "live_autonomous_enabled": True, "live_confirmation": "ENABLE LIVE AUTONOMY"})
        assert accepted.status_code == 200
        assert accepted.json()["live_execution_supported"] is False
        audit = client.get("/api/autonomous-controls/audit").json()["events"]
        assert {item["control_name"] for item in audit} == {"demo_autonomous_enabled", "live_autonomous_enabled"}

    with _client_for("auth0|controls-b") as client:
        isolated = client.get("/api/autonomous-controls").json()
        assert isolated["demo_autonomous_enabled"] is False
        assert isolated["live_autonomous_enabled"] is False


def test_preferences_watchlists_and_safe_status_are_user_scoped(platform_storage):
    with _client_for("auth0|ux-a") as client:
        preferences=client.patch("/api/user-preferences",json={"timezone":"Europe/London",
            "notifications":{"schedule_failed":False}})
        assert preferences.status_code==200 and preferences.json()["timezone"]=="Europe/London"
        initial=client.get("/api/watchlists").json()["watchlists"]
        assert initial[0]["name"]=="Forex Majors"
        custom=client.post("/api/watchlists",json={"name":"Metals","symbols":["OANDA:XAU_USD"]})
        assert custom.status_code==201 and custom.json()["items"][0]["symbol"]=="OANDA:XAU_USD"
        status=client.get("/api/status")
        assert status.status_code==200 and all("worker_id" not in str(item) for item in status.json()["services"])
    with _client_for("auth0|ux-b") as client:
        assert client.get("/api/user-preferences").json()["timezone"]=="America/Chicago"
        assert all(item["name"]!="Metals" for item in client.get("/api/watchlists").json()["watchlists"])


def test_demo_test_endpoint_is_explicitly_non_submitting(platform_storage,monkeypatch):
    user="auth0|dry-run";connection=platform_storage.save_connection(user,base_url="https://demo.test/backend-api",
        username="user",password="password",server="HeroFX",environment="demo")
    platform_storage.sync_accounts(user,connection.connection_ref,{"accounts":[{"accountId":"1","accNum":"12345678","currency":"USD"}]})
    account=platform_storage.list_accounts(user)[0]
    profile=platform_storage.create_profile(user,name="Demo Strategy",account_ref=account["public_id"])
    class FakeRunner:
        async def run(self,*args,**kwargs):
            assert kwargs["dry_run"] is True
            return {"status":"no_trade","outcome":"NO_TRADE","run_id":"dry","reason_codes":["dry_run_trade_candidate"]}
    monkeypatch.setattr(platform,"AutonomousDecisionRunner",lambda:FakeRunner())
    with _client_for(user) as client:
        result=client.post(f"/api/execution-profiles/{profile['public_id']}/demo-test")
    assert result.status_code==200 and result.json()["submission_allowed"] is False and result.json()["dry_run"] is True

def test_profile_decision_engine_update_persists_and_reports_safe_readiness(platform_storage, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    connection = platform_storage.save_connection("auth0|provider-user",
        base_url="https://demo.tradelocker.test/backend-api", username="private-user", password="private-password",
        server="demo", environment="demo")
    platform_storage.sync_accounts("auth0|provider-user", connection.connection_ref,
        {"accounts": [{"accountId": "provider-account", "accNum": "1"}]})
    account = platform_storage.list_accounts("auth0|provider-user")[0]
    profile = platform_storage.create_profile("auth0|provider-user", name="Provider Profile",
        account_ref=account["public_id"])

    with _client_for("auth0|provider-user") as client:
        updated = client.put(f"/api/execution-profiles/{profile['public_id']}", json={
            "decision_provider": "openai", "model_identifier": "gpt-test-model",
            "minimum_confidence": 0.82,
        })
        assert updated.status_code == 200
        saved = updated.json()["profile"]
        assert saved["decision_provider"] == "openai"
        assert saved["model_identifier"] == "gpt-test-model"
        assert saved["minimum_confidence"] == 0.82
        assert saved["provider_readiness"]["label"] == "API Key Missing"
        assert saved["provider_readiness"]["blocking_reasons"] == ["openai_api_key_missing"]
        assert "api_key" not in saved or saved.get("api_key") is None

        rejected = client.put(f"/api/execution-profiles/{profile['public_id']}", json={
            "decision_provider": "openai", "model_identifier": "gpt-test-model",
            "minimum_confidence": 1.01,
        })
        assert rejected.status_code == 422

        missing_model = client.put(f"/api/execution-profiles/{profile['public_id']}", json={
            "decision_provider": "openai", "model_identifier": None, "minimum_confidence": 0.7,
        })
        assert missing_model.status_code == 200
        assert "model_not_selected" in missing_model.json()["profile"]["provider_readiness"]["blocking_reasons"]

    assert ExecutionRepository(platform_storage.db_path).recent_decision_runs("auth0|provider-user") == []


def test_profile_delete_api_requires_exact_name_confirmation(platform_storage):
    connection = platform_storage.save_connection("auth0|delete-user",
        base_url="https://demo.tradelocker.test/backend-api", username="u", password="p",
        server="demo", environment="demo")
    platform_storage.sync_accounts("auth0|delete-user", connection.connection_ref,
        {"accounts": [{"accountId": "a", "accNum": "1"}]})
    account = platform_storage.list_accounts("auth0|delete-user")[0]
    profile = platform_storage.create_profile("auth0|delete-user", name="Exact Profile", account_ref=account["public_id"])

    with _client_for("auth0|delete-user") as client:
        rejected = client.delete(f"/api/execution-profiles/{profile['public_id']}?confirmation_name=wrong")
        assert rejected.status_code == 409
        accepted = client.delete(f"/api/execution-profiles/{profile['public_id']}?confirmation_name=Exact%20Profile")
        assert accepted.status_code == 200

    assert platform_storage.list_profiles("auth0|delete-user") == []


class _DemoStatusDiscoveryClient:
    def __init__(self, **kwargs):
        self.account_id = kwargs["account_id"]
        self.account_number = kwargs["account_number"]

    async def get_accounts(self):
        return {"accounts": [{"accountId": self.account_id, "accNum": self.account_number,
            "name": "Profile Demo", "currency": "USD"}]}

    async def aclose(self):
        pass


class _DemoStatusAccountService:
    async def retrieve(self, user_sub, account_alias):
        return TradeLockerAccountStatus(
            retrieved_at=datetime.now(timezone.utc),
            account=TradeLockerAccountIdentity(account_id="demo-account", account_number="7",
                name="Profile Demo", currency="USD", account_alias=account_alias,
                environment="demo", active=True),
            balance=10_000, projected_balance=10_000, available_funds=9_000,
            blocked_balance=0, cash_balance=10_000, withdrawal_available=9_000,
            open_gross_pnl=0, open_net_pnl=0, positions_count=0, pending_orders_count=0,
            today=TradeLockerTodayStatus(gross=0, net=0, fees=0, volume=0, trades_count=0),
            margin=TradeLockerMarginStatus(initial_requirement=0, maintenance_requirement=0,
                warning_level=100, stop_out_level=50, warning_requirement=0,
                margin_before_warning=9_000),
        )


def test_demo_status_uses_profile_bound_safe_connection_label(platform_storage, monkeypatch):
    monkeypatch.setattr(settings, "tradelocker_demo_base_url", "https://demo.tradelocker.test/backend-api")
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    monkeypatch.setattr(settings, "finnhub_enabled", True)
    monkeypatch.setattr(settings, "finnhub_api_key", "test-provider-key")
    username, password, token = "private-user", "private-password", "private-token"
    demo_connection = platform_storage.save_connection("auth0|user-a",
        base_url=settings.tradelocker_demo_base_url, username=username, password=password,
        server="DEMO-SERVER", environment="demo", label="Safe Demo Connection")
    platform_storage.sync_accounts("auth0|user-a", demo_connection.connection_ref,
        {"accounts": [{"accountId": "demo-account", "accNum": "7", "currency": "USD"}]})
    demo_account = platform_storage.list_accounts("auth0|user-a")[0]
    demo_profile = platform_storage.create_profile("auth0|user-a", name="Profile Demo",
        account_ref=demo_account["public_id"], execution_mode="demo_manual")
    service = AutonomousDemoService(broker_repository=platform_storage,
        execution_repository=ExecutionRepository(platform_storage.db_path),
        client_factory=_DemoStatusDiscoveryClient,
        account_status_service=_DemoStatusAccountService())
    monkeypatch.setattr(platform, "AutonomousDemoService", lambda: service)

    with _client_for("auth0|user-a") as client:
        response = client.get(f"/api/execution-profiles/{demo_profile['public_id']}/demo-status")

    assert response.status_code == 200
    body = response.json()
    assert body["broker"] == "Safe Demo Connection"
    assert body["profile_ref"] == demo_profile["public_id"]
    assert body["account_alias"] == demo_account["account_alias"]
    assert body["confirmed_demo"] is True
    serialized = response.text
    assert username not in serialized and password not in serialized and token not in serialized

    live_connection = platform_storage.save_connection("auth0|user-a",
        base_url="https://live.tradelocker.test/backend-api", username="live-private-user",
        password="live-private-password", server="LIVE-SERVER", environment="live",
        label="Safe Live Connection", create_new=True)
    platform_storage.sync_accounts("auth0|user-a", live_connection.connection_ref,
        {"accounts": [{"accountId": "live-account", "accNum": "8", "currency": "USD"}]})
    live_account = next(item for item in platform_storage.list_accounts("auth0|user-a")
        if item["environment"] == "live")
    live_profile = platform_storage.create_profile("auth0|user-a", name="Live Read Only",
        account_ref=live_account["public_id"], execution_mode="read_only")

    with _client_for("auth0|user-a") as client:
        rejected = client.get(f"/api/execution-profiles/{live_profile['public_id']}/demo-status")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "blocked"
    assert rejected.json()["confirmed_demo"] is False
    assert rejected.json()["can_submit_demo_orders"] is False
    assert "account_not_demo" in rejected.json()["blocking_reasons"]


def test_user_cannot_access_another_users_broker_connection(platform_storage):
    platform_storage.save_connection(
        "auth0|user-b",
        base_url="https://demo.example/backend-api",
        username="user-b",
        password="password-b",
        server="DEMO",
    )

    with _client_for("auth0|user-a") as client:
        response = client.get("/api/broker/status")

    assert response.json()["status"] == "not_connected"
    assert response.json()["connected"] is False
    assert response.json()["selected_account"] is None
    assert platform_storage.get_connection("auth0|user-a") is None


def test_me_is_tied_to_auth0_subject(platform_storage):
    with _client_for("auth0|user-a") as client:
        response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["sub"] == "auth0|user-a"
    with sqlite3.connect(platform_storage.db_path) as connection:
        stored = connection.execute("SELECT auth0_sub FROM users").fetchall()
    assert stored == [("auth0|user-a",)]


def test_password_is_encrypted_at_rest(platform_storage):
    password = "plain-text-password"
    platform_storage.save_connection(
        "auth0|user-a",
        base_url="https://demo.example/backend-api",
        username="user-a",
        password=password,
        server="DEMO",
    )

    with sqlite3.connect(platform_storage.db_path) as connection:
        encrypted = connection.execute(
            "SELECT password_encrypted FROM broker_connections"
        ).fetchone()[0]

    assert password.encode() not in encrypted
    assert platform_storage.get_connection("auth0|user-a").password == password


def test_select_account_invalidates_user_scoped_config_cache(platform_storage):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.tradelocker.com/backend-api",
        username="user-a", password="secret", server="HEROFX",
    )
    key = TradeLockerConfigCacheKey("auth0|user-a", "demo", "HEROFX", "old", "1")
    tradelocker_config_cache.put(key, {"d": {}})
    try:
        with _client_for("auth0|user-a") as client:
            response = client.post(
                "/api/broker/tradelocker/select-account",
                json={"account_id": "new", "account_number": "2"},
            )
        assert response.status_code == 200
        assert tradelocker_config_cache.get(key) is None
    finally:
        tradelocker_config_cache.clear()


def test_saved_credentials_discover_accounts_without_returning_secrets(
    platform_storage, monkeypatch
):
    password = "private-password"
    token = "private-jwt-token"
    captured = {}
    discovered_accounts = [
        {"accountId": 12345, "accNum": 2, "name": "First", "currency": "USD"},
        {"accountId": 12346, "accNum": 3, "name": "Second", "currency": "EUR"},
        {"accountId": 12347, "accNum": 4, "name": "Third", "currency": "GBP"},
    ]

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_accounts(self):
            return {"accounts": discovered_accounts}

    monkeypatch.setattr(platform, "TradeLockerClient", FakeClient)
    with _client_for("auth0|user-a") as client:
        saved = client.post(
            "/api/broker/tradelocker/save-credentials",
            json={
                "base_url": "https://demo.example/backend-api",
                "username": "user-a",
                "password": password,
                "server": "DEMO",
            },
        )
        discovered = client.post("/api/broker/tradelocker/discover-accounts")

    assert saved.json()["status"] == "connected_no_account"
    assert discovered.json() == {"accounts": discovered_accounts}
    stored_accounts = platform_storage.list_accounts("auth0|user-a")
    assert len(stored_accounts) == 3
    assert not any(account["is_default_analysis"] for account in stored_accounts)
    assert captured["password"] == password
    output = saved.text + discovered.text
    assert password not in output
    assert token not in output
    assert "password_encrypted" not in output


def test_invalid_tradelocker_credentials_are_rejected_before_storage(platform_storage, monkeypatch):
    class RejectedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get_accounts(self):
            raise TradeLockerError("login", "Rejected", code="http_error", status_code=400)

    monkeypatch.setattr(platform, "TradeLockerClient", RejectedClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/tradelocker/save-credentials", json={
            "base_url": "https://live.tradelocker.com/backend-api",
            "username": "user-a@example.test", "password": "wrong",
            "server": "DEMO",
        })
    assert response.status_code == 401
    assert response.json()["error"] == "tradelocker_credentials_rejected"
    assert "rejected the credentials or server selection" in response.json()["message"]
    assert response.json()["request_id"]
    assert platform_storage.get_connection("auth0|user-a") is None
    assert "wrong" not in response.text


def test_tradelocker_upstream_failure_has_structured_service_error(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.tradelocker.com/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )

    class FailedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get_accounts(self):
            raise TradeLockerError("get_accounts", "Timeout", code="timeout")

    monkeypatch.setattr(platform, "TradeLockerClient", FailedClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/tradelocker/discover-accounts")
    assert response.status_code == 502
    assert response.json()["error"] == "tradelocker_account_discovery_failed"
    assert response.json()["message"] == "Unable to retrieve TradeLocker accounts."


def test_tradelocker_account_parsing_error_is_structured(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.tradelocker.com/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )

    class MalformedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get_accounts(self):
            raise TradeLockerError(
                "get_accounts", "Unusable accounts response", code="invalid_response"
            )

    monkeypatch.setattr(platform, "TradeLockerClient", MalformedClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/tradelocker/discover-accounts")
    assert response.status_code == 502
    assert response.json()["error"] == "tradelocker_account_discovery_failed"
    assert "http_error" not in response.text


def test_shared_workspace_discovery_needs_no_application_session(platform_storage):
    with TestClient(app) as client:
        response = client.post("/api/broker/tradelocker/discover-accounts")
    assert response.status_code == 200
    assert response.json()["status"] == "not_connected"
    assert response.json()["connected"] is False


def test_selected_account_is_stored_per_user(platform_storage):
    platform_storage.save_connection(
        "auth0|user-a",
        base_url="https://demo.example/backend-api",
        username="user-a",
        password="password-a",
        server="DEMO",
    )
    with _client_for("auth0|user-a") as client:
        response = client.post(
            "/api/broker/tradelocker/select-account",
            json={"account_id": "12345", "account_number": "2"},
        )

    stored = platform_storage.get_connection("auth0|user-a")
    assert response.json()["status"] == "ready"
    assert stored.account_id == "12345"
    assert stored.account_number == "2"


def test_onboarding_status_requires_valid_discovered_selected_account(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )
    platform_storage.select_account("auth0|user-a", "12345", "2")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_accounts(self):
            return {"accounts": [{"accountId": 12345, "accNum": 2}]}

    monkeypatch.setattr(platform, "TradeLockerClient", FakeClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/onboarding-status")

    assert response.json()["status"] == "ready"
    assert "password" not in response.text.lower()


def test_onboarding_status_rejects_revoked_tradelocker_credentials(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )
    platform_storage.select_account("auth0|user-a", "12345", "2")

    class RevokedClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_accounts(self):
            raise TradeLockerError("get_accounts", "Unauthorized", code="unauthorized")

    monkeypatch.setattr(platform, "TradeLockerClient", RevokedClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/onboarding-status")

    assert response.json()["status"] == "invalid_credentials"
    assert "Unauthorized" not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status", "retryable"),
    [
        (TradeLockerError("get_accounts", "Expired", code="token_expired"), "expired", False),
        (TradeLockerError("get_accounts", "Timeout", code="timeout"), "unavailable", True),
    ],
)
def test_onboarding_status_classifies_expired_and_unavailable_connections(
    platform_storage, monkeypatch, error, expected_status, retryable
):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )

    class FailedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get_accounts(self): raise error

    monkeypatch.setattr(platform, "TradeLockerClient", FailedClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/onboarding-status")
    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert response.json()["connected"] is False
    assert response.json()["selected_account"] is None
    assert response.json()["retryable"] is retryable


def test_onboarding_status_connected_without_account(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )

    class ValidClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get_accounts(self): return {"accounts": [{"accountId": 123, "accNum": 1}]}

    monkeypatch.setattr(platform, "TradeLockerClient", ValidClient)
    with _client_for("auth0|user-a") as client:
        response = client.post("/api/broker/onboarding-status")
    assert response.json() == {
        "status": "connected_no_account", "connected": True,
        "selected_account": None, "message": None, "retryable": False,
    }


@pytest.mark.asyncio
async def test_mcp_tool_uses_current_users_saved_connection(platform_storage, monkeypatch):
    platform_storage.save_connection(
        "auth0|user-a",
        base_url="https://demo.example/backend-api",
        username="user-a",
        password="password-a",
        server="DEMO-A",
    )
    platform_storage.save_connection(
        "auth0|user-b",
        base_url="https://demo.example/backend-api",
        username="user-b",
        password="password-b",
        server="DEMO-B",
    )

    class FakeClient:
        def __init__(self, **kwargs):
            self.username = kwargs["username"]

        async def get_accounts(self):
            return {"accounts": [{"name": self.username}]}

    monkeypatch.setattr(adapter, "TradeLockerClient", FakeClient)
    identity_token = set_current_claims(_claims("auth0|user-a"))
    try:
        result = await tools.get_tradelocker_accounts()
    finally:
        reset_current_claims(identity_token)

    assert result == {"accounts": [{"name": "user-a"}]}
    assert "user-b" not in str(result)
    assert "password-a" not in str(result)


def test_private_engine_does_not_enable_browser_cors():
    with TestClient(app) as client:
        response = client.options(
            "/api/me",
            headers={
                "Origin": settings.frontend_origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 405
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_mcp_missing_connection_returns_safe_setup_url(platform_storage, monkeypatch):
    monkeypatch.setattr(settings, "frontend_origin", "https://setup.example.test/")
    identity_token = set_current_claims(_claims("auth0|not-connected"))
    try:
        result = await tools.get_my_tradelocker_symbols()
        status = tools.get_tradelocker_connection_status()
    finally:
        reset_current_claims(identity_token)

    expected_url = (
        "https://setup.example.test/connect-tradelocker"
    )
    assert result == {
        "status": "setup_required",
        "message": "TradeLocker setup required.",
        "setup_url": expected_url,
        "instruction": (
            "Open the setup URL, connect TradeLocker using the same login account, "
            "then retry in the desk assistant."
        ),
    }
    assert status["setup_url"] == expected_url
    assert status["connected"] is False
    assert status["selected_account"] is False
    serialized = str(result).lower()
    assert "password" not in serialized
    assert "token" not in serialized
    assert "username" not in serialized


@pytest.mark.asyncio
async def test_authorized_user_with_revoked_tradelocker_gets_setup_recovery(
    platform_storage, monkeypatch
):
    platform_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="password-a", server="DEMO",
    )
    client = type("Client", (), {
        "get_symbols": AsyncMock(
            side_effect=TradeLockerError(
                "get_symbols", "Unauthorized", code="http_error", status_code=401
            )
        )
    })()
    monkeypatch.setattr(tools, "get_tradelocker_adapter", lambda: type("Adapter", (), {"client": client})())
    identity_token = set_current_claims(_claims("auth0|user-a"))
    try:
        result = await tools.get_my_tradelocker_symbols()
    finally:
        reset_current_claims(identity_token)
    assert result["status"] == "setup_required"
    assert "setup_url" in result
