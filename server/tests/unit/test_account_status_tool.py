from datetime import datetime, timezone

import pytest

from app.auth.identity import reset_current_claims, set_current_claims
from app.services import desk_tools as tools
from app.models.tradelocker import (
    TradeLockerAccountIdentity,
    TradeLockerAccountStatus,
    TradeLockerMarginStatus,
    TradeLockerTodayStatus,
)


def normalized_status() -> TradeLockerAccountStatus:
    return TradeLockerAccountStatus(
        retrieved_at=datetime.now(timezone.utc),
        account=TradeLockerAccountIdentity(
            account_id="780896", account_number="2", name="HEROFX#2",
            currency="USD", environment="demo", active=True,
        ),
        balance=0, projected_balance=0, available_funds=0, blocked_balance=0,
        cash_balance=0, withdrawal_available=0, open_gross_pnl=0, open_net_pnl=0,
        positions_count=0, pending_orders_count=0,
        today=TradeLockerTodayStatus(gross=0, net=0, fees=0, volume=0, trades_count=0),
        margin=TradeLockerMarginStatus(
            initial_requirement=0, maintenance_requirement=0, warning_level=100,
            stop_out_level=166.67, warning_requirement=0, margin_before_warning=0,
        ),
    )


@pytest.mark.asyncio
async def test_mcp_account_status_never_returns_raw_or_paper_data(monkeypatch):
    expected = normalized_status()

    class Service:
        async def retrieve(self, user):
            assert user == "auth0|user-a"
            return expected

    monkeypatch.setattr(tools, "TradeLockerAccountStatusService", Service)
    token = set_current_claims({"sub": "auth0|user-a"})
    try:
        result = await tools.get_account_status()
        alias = await tools.get_my_tradelocker_account_status()
    finally:
        reset_current_claims(token)
    for payload in (result, alias):
        assert payload["source"] == "tradelocker"
        assert payload["balance"] == 0.0
        assert "accountDetailsData" not in str(payload)
        assert "paper" not in str(payload)


@pytest.mark.asyncio
async def test_mcp_account_status_requires_authenticated_user():
    result = await tools.get_account_status()
    assert result["status"] == "unavailable"
    assert result["error"] == "authentication_required"


@pytest.mark.asyncio
async def test_paper_status_is_explicitly_separate():
    result = await tools.get_paper_account_status()
    assert result["source"] == "paper"
    assert result["environment"] == "paper"
