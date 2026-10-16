from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.crypto import VersionedSecretCipher
from app.config.settings import Settings
from app.models.execution_profile_v2 import ExecutionProfileV2
from app.providers.contracts import ExecutionProvider, ProviderCapabilities
from app.providers.registry import provider_registry
from app.providers.robinhood import RobinhoodMcpExecutionProvider
from app.providers.tradelocker import TradeLockerExecutionProvider
from app.providers.tradingview import TradingViewChartProvider, TradingViewSignalProvider
from app.services.instruments import instrument_mapper
from app.storage.runtime_settings import RuntimeSettingsRepository
from app.storage.brokers import BrokerRepository
from app.config.settings import settings


def test_provider_registry_and_capabilities_fail_closed():
    assert provider_registry.execution("tradelocker") is not None
    assert provider_registry.execution("tradingview_chart") is None
    assert provider_registry.require("tradingview_chart").broker_name is None
    assert ProviderCapabilities().public()["order_submission"] is False
    assert provider_registry.require("robinhood_mcp").status == "not_configured"
    assert RobinhoodMcpExecutionProvider().get_capabilities().options is True
    provider = object.__new__(TradeLockerExecutionProvider)
    assert isinstance(provider, ExecutionProvider)
    assert provider.get_capabilities().account_snapshot is True


@pytest.mark.asyncio
async def test_robinhood_boundary_is_explicitly_non_operational():
    provider = RobinhoodMcpExecutionProvider()
    assert await provider.discover_accounts() == []
    with pytest.raises(NotImplementedError): await provider.connect()


def test_provider_specific_symbol_mapping_is_independent():
    instrument = instrument_mapper.resolve("forex:EUR/USD")
    assert instrument.provider_symbols["tradingview"] == "OANDA:EURUSD"
    assert instrument.provider_symbols["finnhub"] == "OANDA:EUR_USD"
    assert instrument.provider_symbols["tradingview"] != instrument.provider_symbols["finnhub"]
    assert instrument_mapper.resolve("equity:AAPL").provider_symbols["tradingview"] == "AAPL"
    assert instrument_mapper.resolve("index:SPX").provider_symbols["tradingview"] == "SPX"
    assert instrument_mapper.resolve("metal:XAU/USD").provider_symbols["tradingview"] == "OANDA:XAUUSD"
    assert instrument_mapper.resolve("crypto:BTC/USD").provider_symbols["tradingview"] == "COINBASE:BTCUSD"
    assert TradingViewChartProvider().chart_configuration("forex:EUR/USD")["execution_authoritative"] is False
    assert TradingViewSignalProvider().create_trade_intent({"signal":"buy"})["can_submit_order"] is False


def test_runtime_settings_use_storage_and_code_defaults(tmp_path: Path):
    repository = RuntimeSettingsRepository(tmp_path / "runtime.db")
    assert repository.get_all()["maintenance_mode"] is False
    assert repository.update({"maintenance_mode": True})["maintenance_mode"] is True
    assert RuntimeSettingsRepository(tmp_path / "runtime.db").get_all()["maintenance_mode"] is True


def test_technical_defaults_do_not_require_environment_overrides():
    defaults = Settings(_env_file=None)
    assert defaults.finnhub_timeout_seconds == 15
    assert defaults.finnhub_max_retries == 2
    assert defaults.autonomous_default_minimum_confidence == .70
    user_storage = Path("app/storage/user_experience.py").read_text()
    assert "os.getenv" not in user_storage and "os.environ" not in user_storage


def test_v1_ciphertext_remains_readable_after_v2_activation():
    v1 = VersionedSecretCipher({"v1": "old-key"}, "v1")
    ciphertext, version = v1.encrypt("credential")
    rotated = VersionedSecretCipher({"v1": "old-key", "v2": "new-key"}, "v2")
    assert rotated.decrypt(ciphertext, version) == "credential"
    migrated, new_version = rotated.reencrypt(ciphertext, version)
    assert new_version == "v2" and rotated.decrypt(migrated, new_version) == "credential"


def test_stored_v1_connection_survives_v2_activation_and_migrates(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "broker_secret_keys_json", '{"v1":"old-key","v2":"new-key"}')
    monkeypatch.setattr(settings, "active_encryption_key_version", "v1")
    repository = BrokerRepository(tmp_path / "rotation.db", "old-key")
    saved = repository.save_connection("user", base_url="https://demo.example", username="u",
        password="credential", server="HeroFX")
    monkeypatch.setattr(settings, "active_encryption_key_version", "v2")
    assert repository.get_connection("user", saved.connection_ref).password == "credential"
    assert repository.reencrypt_credentials() == 1
    assert repository.get_connection("user", saved.connection_ref).password == "credential"


def test_asset_specific_strategy_extensions_validate_without_forex_coercion():
    equities = ExecutionProfileV2.model_validate({"asset_class":"equities","forex":None,"equities":{"sizing":"fractional"}})
    assert equities.equities.sizing == "fractional"
    options = ExecutionProfileV2.model_validate({"asset_class":"options","forex":None,"options":{"maximum_premium":500}})
    assert options.options.maximum_contracts == 1
    with pytest.raises(ValidationError): ExecutionProfileV2.model_validate({"asset_class":"options","forex":None})
