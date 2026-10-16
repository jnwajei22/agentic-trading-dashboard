from fastapi.testclient import TestClient
from app.main import app
from app.config.settings import settings
from app.storage.brokers import BrokerRepository


def test_no_login_and_caller_cannot_select_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "desk.db"))
    monkeypatch.setattr(settings, "workspace_owner_sub", "workspace-owner")
    monkeypatch.setattr(settings, "broker_secret_key", "test-secret")
    repo = BrokerRepository()
    repo.ensure_user("other-owner", "private@example.test")
    app.dependency_overrides.clear()
    client = TestClient(app)
    for headers in [{}, {"Authorization": "Bearer forged", "X-User-Sub": "other-owner"}]:
        result = client.get("/api/me?sub=other-owner", headers=headers)
        assert result.status_code == 200
        assert result.json() == {"sub": "workspace-owner", "email": None}
        assert client.get("/api/broker/accounts", headers=headers).json() == {"accounts": []}
        history = client.get("/api/assistant/conversations", headers=headers)
        assert history.status_code == 200
        assert history.json() == {"conversations": []}


def test_retired_mcp_and_oauth_routes_are_not_mounted():
    paths = set(app.openapi()["paths"])
    assert not any(path.startswith(("/mcp", "/oauth")) for path in paths)
