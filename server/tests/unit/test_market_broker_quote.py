import pytest
from app.api.routes import platform


@pytest.mark.asyncio
async def test_market_summary_maps_compact_broker_quote_fields_with_owner_scope(monkeypatch):
    async def instruments(owner, alias):
        assert (owner, alias) == ("owner-1", "my-demo")
        return {"base_url":"https://example.test", "username":"test", "password":"test", "server":"demo", "account_id":"1", "account_number":"1", "environment":"demo"}, [{"symbol":"EURUSD", "broker_symbol":"EURUSD"}]

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_quote(self, symbol):
            assert symbol == "EURUSD"
            return {"d":{"bp":1.1,"ap":1.2}}

    monkeypatch.setattr(platform, "_account_instruments", instruments)
    monkeypatch.setattr(platform, "TradeLockerClient", Client)
    quote, tradability = await platform._execution_market_context("owner-1", "my-demo", "forex:EUR/USD")
    assert quote["bid"] == 1.1
    assert quote["ask"] == 1.2
    assert quote["price"] == pytest.approx(1.15)
    assert tradability["available"] is True
