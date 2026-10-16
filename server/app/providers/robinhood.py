from __future__ import annotations

from typing import Any

from app.providers.contracts import ProviderCapabilities


class RobinhoodMcpExecutionProvider:
    """Official MCP/OAuth boundary only. No unofficial transport is implemented."""
    provider_type = "robinhood_mcp"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(asset_classes=("equities", "options"), options=True, fractional_quantity=True)

    async def connect(self, **credentials: Any) -> Any:
        raise NotImplementedError("Robinhood Agentic MCP authentication is not configured.")

    async def discover_accounts(self) -> list[dict[str, Any]]:
        return []
