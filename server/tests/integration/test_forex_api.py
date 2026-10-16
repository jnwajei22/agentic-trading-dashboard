from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.routes import forex
from app.config.settings import settings
from app.brokers.tradelocker.adapter import TradeLockerAdapter
from app.main import app
from app.services.watchlist import DEFAULT_FOREX_WATCHLIST


client = TestClient(app)


def test_get_forex_watchlist():
    response = client.get("/forex/watchlist")

    assert response.status_code == 200
    assert [item["pair"] for item in response.json()] == DEFAULT_FOREX_WATCHLIST


def test_chart_and_scan_routes_are_removed():
    assert client.post("/forex/chart", json={"pair": "EUR/USD"}).status_code == 404
    assert client.post("/forex/scan", json={"candle_data": {}}).status_code == 404
    assert client.get("/charts/chart_1234567890.png").status_code == 404


def test_order_preview_returns_rejected_object_with_risk_violations(monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", False)
    response = client.post(
        "/forex/order-preview",
        json={
            "pair": "EUR/USD",
            "side": "long",
            "entry": 1.1000,
            "stop_loss": 1.1010,
            "take_profit": 1.1100,
            "risk_percent": 0.5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert any("Long stop loss" in violation for violation in body["violations"])
    assert set(body) == {
        "preview_id", "status", "pair", "side", "entry", "stop_loss",
        "take_profit", "lot_size", "pip_risk", "risk_amount",
        "reward_risk", "violations", "expires_at",
    }


def test_forex_routes_never_invoke_live_trading(monkeypatch):
    submit_order = AsyncMock(side_effect=AssertionError("live broker invoked"))
    monkeypatch.setattr(TradeLockerAdapter, "submit_order", submit_order)
    monkeypatch.setattr(settings, "kill_switch_enabled", True)

    response = client.post(
        "/forex/order-preview",
        json={
            "pair": "EUR/USD",
            "side": "short",
            "entry": 1.1000,
            "stop_loss": 1.1050,
            "take_profit": 1.0900,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "preview_only"
    submit_order.assert_not_awaited()
