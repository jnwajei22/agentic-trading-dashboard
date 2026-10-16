from __future__ import annotations

from app.providers.contracts import ProviderCapabilities, ProviderDescriptor


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}

    def register(self, descriptor: ProviderDescriptor) -> None:
        self._providers[descriptor.provider_type] = descriptor

    def get(self, provider_type: str) -> ProviderDescriptor | None:
        return self._providers.get(provider_type)

    def require(self, provider_type: str) -> ProviderDescriptor:
        provider = self.get(provider_type)
        if not provider:
            raise KeyError(provider_type)
        return provider

    def all(self) -> list[ProviderDescriptor]:
        return list(self._providers.values())

    def execution(self, provider_type: str) -> ProviderDescriptor | None:
        provider = self.get(provider_type)
        return provider if provider and provider.execution_provider else None


provider_registry = ProviderRegistry()
provider_registry.register(ProviderDescriptor(
    "tradelocker", "TradeLocker", "trading_platform", ("execution", "account_data", "broker_market_data"),
    platform_name="TradeLocker", status="available",
    capabilities=ProviderCapabilities(
        asset_classes=("forex", "metals", "indices", "energy", "crypto", "cfds"),
        supported_order_types=("market", "limit", "stop"), account_snapshot=True,
        positions=True, pending_orders=True, order_preview=True, order_submission=True,
        cancellation=True, partial_close=True, trailing_stop=True, paper_or_demo=True,
        live=True, autonomous_demo=True, market_data=True,
    ),
))
provider_registry.register(ProviderDescriptor(
    "tradingview_chart", "TradingView", "chart_provider", ("chart",), status="available",
    capabilities=ProviderCapabilities(asset_classes=("forex", "equities", "options", "indices", "metals", "crypto")),
))
provider_registry.register(ProviderDescriptor(
    "tradingview_signal", "TradingView Signals", "signal_provider", ("signal",), status="not_configured",
))
provider_registry.register(ProviderDescriptor(
    "robinhood_mcp", "Robinhood Agentic", "trading_platform", ("execution", "account_data"),
    broker_name="Robinhood Financial", platform_name="Robinhood Agentic", status="not_configured",
    capabilities=ProviderCapabilities(asset_classes=("equities", "options"), options=True, fractional_quantity=True),
))
for reserved, name in (("alpaca", "Alpaca"), ("interactive_brokers", "Interactive Brokers")):
    provider_registry.register(ProviderDescriptor(reserved, name, "reserved", (), status="unsupported"))
