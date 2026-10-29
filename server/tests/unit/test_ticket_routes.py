from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import trading
from app.models.autonomous import AutonomousOrderProposal


def profile(**overrides):
    return {"account_id":"account-a", "account_alias":"demo", "enabled":True,
            "execution_mode":"demo_manual", "account_environment":"demo", "is_demo":1, **overrides}


def setup(monkeypatch, *, kill=False, **overrides):
    monkeypatch.setattr(trading, "BrokerRepository", lambda: SimpleNamespace(get_profile=lambda user, ref: profile(**overrides) if user=="owner" else None))
    monkeypatch.setattr(trading, "ExecutionRepository", lambda: SimpleNamespace(get_autonomous_controls=lambda _: {"global_autonomous_kill_switch":kill}))


@pytest.mark.parametrize("overrides", [{"enabled":False}, {"execution_mode":"read_only"}, {"execution_mode":"demo_autonomous"}, {"account_environment":"live"}, {"is_demo":0}])
def test_ticket_cannot_promote_disabled_readonly_autonomous_or_live_profile(monkeypatch, overrides):
    setup(monkeypatch, **overrides)
    with pytest.raises(HTTPException): trading.ticket_profile("owner", "profile", "account-a")


def test_ticket_checks_owner_account_and_kill_switch_but_allows_risk_reduction(monkeypatch):
    setup(monkeypatch, kill=True)
    for user, account in [("other", "account-a"), ("owner", "account-b"), ("owner", "account-a")]:
        with pytest.raises(HTTPException): trading.ticket_profile(user, "profile", account)
    assert trading.ticket_profile("owner", "profile", opening=False)["account_id"] == "account-a"


def test_ticket_submission_requires_true_confirmation_and_rejects_extra_routing():
    for extra in [{"confirmed":False}, {"confirmed":True, "account_id":"other"}]:
        with pytest.raises(ValidationError):
            trading.ConfirmSubmission(preview_id="preview", idempotency_key="durable-key", **extra)


def test_broker_rows_preserve_zero_and_strip_unneeded_fields():
    row=trading.public_row({"id":"p1", "tradableInstrumentId":42, "qty":0, "openPnl":0, "username":"private", "password":"private"}, {"42":"EURUSD"}, "positions")
    assert row["quantity"] == 0 and row["symbol"] == "EURUSD"
    assert "password" not in row and "username" not in row


@pytest.mark.asyncio
async def test_preview_uses_fresh_snapshot_and_exact_requested_quantity(monkeypatch):
    setup(monkeypatch)
    seen={}
    class Engine:
        async def snapshot(self, user, ref, pair):
            return {"snapshot_id":"snapshot", "market":{"pairs":{"EURUSD":{"quote":{"bid":1.1, "ask":1.1002},"instrument_metadata":{"pip_size":.0001,"price_precision":5}}}}}
        _quote=staticmethod(lambda q:(q["bid"], q["ask"]))
        async def review(self, user, ref, proposal, **kwargs):
            seen.update(proposal=proposal, **kwargs)
            return {"preview_id":"preview", "lot_size":kwargs["requested_lots"]}
    monkeypatch.setattr(trading, "AutonomousDemoService", Engine)
    payload=trading.TicketPreview(profile_ref="profile", account_id="account-a", instrument_id="forex:EUR/USD", side="buy",
        quantity={"value":.01,"unit":"lot"}, stop_loss={"mode":"pips","value":20}, take_profit={"mode":"reward_multiple","value":2})
    result=await trading.preview_ticket(payload, {"sub":"owner"})
    assert result["lot_size"] == .01 and seen["requested_lots"] == .01
    assert seen["proposal"].entry == 1.1002
    assert seen["proposal"].stop_loss == 1.0982
    assert seen["proposal"].take_profit == 1.1042


def test_asian_sessions_are_recognized_without_changing_london_or_new_york():
    from datetime import datetime, timezone
    assert "tokyo" in trading.AutonomousDecisionRunner._session(datetime(2026,10,29,6,tzinfo=timezone.utc))
    assert "london" not in trading.AutonomousDecisionRunner._session(datetime(2026,10,29,6,tzinfo=timezone.utc))
    assert "sydney" in trading.AutonomousDecisionRunner._session(datetime(2026,10,29,22,tzinfo=timezone.utc))


def test_explicit_session_restriction_is_enforced(monkeypatch):
    setup(monkeypatch, profile_v2={"forex":{"sessions":["tokyo"]}})
    monkeypatch.setattr(trading.AutonomousDecisionRunner, "_session", staticmethod(lambda _: {"london"}))
    with pytest.raises(HTTPException): trading.ticket_profile("owner", "profile", "account-a")
