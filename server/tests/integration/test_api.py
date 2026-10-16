from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app


client = TestClient(app)
def signal(): return {
    "source": "tradingview",
    "pair": "EUR/USD",
    "timeframe": "1h",
    "signal": "buy",
    "price": 1.1,
    "strategy": "test",
    "timestamp": datetime.now(timezone.utc).isoformat(),
}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_ready():
    assert client.get("/ready").json() == {"status": "ready"}


def test_tradingview_webhook_secret_validation(monkeypatch):
    monkeypatch.setattr(settings, "tradingview_webhook_secret", "test-secret")
    assert client.post("/webhooks/tradingview", json=signal()).status_code == 401
    assert client.post(
        "/webhooks/tradingview",
        json=signal(),
        headers={"X-TradingView-Secret": "wrong"},
    ).status_code == 401
    assert client.post(
        "/webhooks/tradingview",
        json=signal(),
        headers={"X-TradingView-Secret": "test-secret"},
    ).status_code == 202


def test_tradingview_rejects_unknown_forex_pair(monkeypatch):
    monkeypatch.setattr(settings, "tradingview_webhook_secret", "test-secret")
    response = client.post(
        "/webhooks/tradingview",
        json={**signal(), "pair": "XYZ/ABC"},
        headers={"X-TradingView-Secret": "test-secret"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Pair is not allowed."


def test_tradingview_duplicate_is_ignored_and_never_submits(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tradingview_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "signals.db"))
    headers = {"X-TradingView-Secret":"test-secret", "Idempotency-Key":"same-alert"}
    first = client.post("/webhooks/tradingview", json=signal(), headers=headers)
    second = client.post("/webhooks/tradingview", json=signal(), headers=headers)
    assert first.status_code == second.status_code == 202
    assert second.json()["status"] == "duplicate_ignored"
    assert second.json()["order_submitted"] is False and second.json()["can_place_trade"] is False
