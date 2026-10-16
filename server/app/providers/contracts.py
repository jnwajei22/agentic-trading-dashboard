from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


CAPABILITY_FIELDS = (
    "account_snapshot", "positions", "pending_orders", "order_preview",
    "order_submission", "cancellation", "partial_close", "trailing_stop",
    "paper_or_demo", "live", "autonomous_demo", "autonomous_live",
    "market_data", "fractional_quantity", "options", "extended_hours",
)


@dataclass(frozen=True)
class ProviderCapabilities:
    asset_classes: tuple[str, ...] = ()
    supported_order_types: tuple[str, ...] = ()
    account_snapshot: bool = False
    positions: bool = False
    pending_orders: bool = False
    order_preview: bool = False
    order_submission: bool = False
    cancellation: bool = False
    partial_close: bool = False
    trailing_stop: bool = False
    paper_or_demo: bool = False
    live: bool = False
    autonomous_demo: bool = False
    autonomous_live: bool = False
    market_data: bool = False
    fractional_quantity: bool = False
    options: bool = False
    extended_hours: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_type: str
    display_name: str
    category: str
    roles: tuple[str, ...]
    broker_name: str | None = None
    platform_name: str | None = None
    status: str = "not_configured"
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

    @property
    def execution_provider(self) -> bool:
        return "execution" in self.roles

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["execution_provider"] = self.execution_provider
        return value


@runtime_checkable
class ExecutionProvider(Protocol):
    provider_type: str
    async def connect(self, **credentials: Any) -> Any: ...
    async def disconnect(self) -> None: ...
    async def refresh_auth(self) -> Any: ...
    async def discover_accounts(self) -> list[dict[str, Any]]: ...
    def get_capabilities(self) -> ProviderCapabilities: ...
    async def search_instruments(self, query: str) -> list[dict[str, Any]]: ...
    async def get_account_snapshot(self, account_ref: str) -> dict[str, Any]: ...
    async def get_positions(self, account_ref: str) -> list[dict[str, Any]]: ...
    async def get_orders(self, account_ref: str) -> list[dict[str, Any]]: ...
    async def preview_order(self, account_ref: str, order: dict[str, Any]) -> dict[str, Any]: ...
    async def submit_order(self, account_ref: str, preview: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel_order(self, account_ref: str, order_id: str) -> dict[str, Any]: ...
    async def close_position(self, account_ref: str, position_id: str) -> dict[str, Any]: ...
    async def reconcile_order(self, account_ref: str, order_id: str) -> dict[str, Any]: ...


class MarketDataProvider(Protocol):
    async def search(self, query: str) -> list[dict[str, Any]]: ...
    async def quote(self, canonical_id: str) -> dict[str, Any] | None: ...
    async def candles(self, canonical_id: str, timeframe: str) -> list[dict[str, Any]]: ...
    async def market_status(self, canonical_id: str) -> dict[str, Any]: ...
    async def news(self, canonical_id: str) -> list[dict[str, Any]]: ...
    async def calendar(self, canonical_id: str) -> list[dict[str, Any]]: ...
    async def macro_context(self, canonical_id: str) -> dict[str, Any]: ...


class SignalProvider(Protocol):
    def validate_signal(self, payload: bytes, signature: str | None) -> dict[str, Any]: ...
    def normalize_signal(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def deduplicate_signal(self, signal: dict[str, Any]) -> bool: ...
    def create_trade_intent(self, signal: dict[str, Any]) -> dict[str, Any]: ...


class ChartProvider(Protocol):
    def chart_configuration(self, canonical_id: str) -> dict[str, Any]: ...
    def symbol_mapping(self, canonical_id: str) -> str: ...
    def datafeed_metadata(self) -> dict[str, Any]: ...
