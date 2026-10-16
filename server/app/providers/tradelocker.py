from __future__ import annotations

from typing import Any

from app.brokers.tradelocker.adapter import TradeLockerAdapter
from app.providers.contracts import ProviderCapabilities
from app.providers.registry import provider_registry


class TradeLockerExecutionProvider:
    provider_type = "tradelocker"

    def __init__(self, adapter: TradeLockerAdapter | None = None) -> None:
        self.adapter = adapter or TradeLockerAdapter()

    def get_capabilities(self) -> ProviderCapabilities:
        return provider_registry.require(self.provider_type).capabilities

    async def connect(self, **credentials: Any) -> dict[str, Any]:
        return {"status": "connected", "provider_type": self.provider_type}

    async def disconnect(self) -> None:
        client = getattr(self.adapter, "client", None)
        if client and hasattr(client, "aclose"):
            await client.aclose()

    async def refresh_auth(self) -> dict[str, Any]:
        return await self.adapter.get_account()

    async def discover_accounts(self) -> list[dict[str, Any]]:
        payload = await self.adapter.client.get_accounts()
        return self.adapter.client._account_rows(payload)

    async def search_instruments(self, query: str) -> list[dict[str, Any]]:
        payload = await self.adapter.client.get_symbols()
        rows = self.adapter.client._instrument_rows(payload)
        query = query.lower()
        return [row for row in rows if query in str(row).lower()]

    async def get_account_snapshot(self, account_ref: str) -> dict[str, Any]:
        return await self.adapter.get_account()

    async def get_positions(self, account_ref: str) -> list[dict[str, Any]]:
        value = await self.adapter.get_open_positions()
        return value if isinstance(value, list) else value.get("positions", [])

    async def get_orders(self, account_ref: str) -> list[dict[str, Any]]:
        value = await self.adapter.client.get_orders()
        return value if isinstance(value, list) else value.get("orders", [])

    async def preview_order(self, account_ref: str, order: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use the backend execution preview service.")

    async def submit_order(self, account_ref: str, preview: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use the authorized backend submission service.")

    async def cancel_order(self, account_ref: str, order_id: str) -> dict[str, Any]:
        return await self.adapter.client.cancel_order(order_id)

    async def close_position(self, account_ref: str, position_id: str) -> dict[str, Any]:
        return await self.adapter.client.close_position(position_id, strategy_id="provider-interface")

    async def reconcile_order(self, account_ref: str, order_id: str) -> dict[str, Any]:
        return {"provider_type": self.provider_type, "order_id": order_id, "status": "reconciliation_required"}
