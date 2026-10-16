from __future__ import annotations

import logging

import pytest

from app.services.tradelocker.account_status import (
    AccountStatusUnavailable,
    TradeLockerAccountStatusService,
)
from app.services.tradelocker.config_cache import (
    TradeLockerConfigCache,
    TradeLockerConfigCacheKey,
)
from app.storage.brokers import BrokerConnection
from tests.unit.test_tradelocker_mapping import FIELDS, VALUES, config, state


def connection(
    *, user: str = "user-a", environment: str = "demo", account_id: str = "780896"
) -> BrokerConnection:
    return BrokerConnection(
        connection_id=f"connection-{user}",
        base_url=f"https://{environment}.tradelocker.test/backend-api",
        username=f"{user}@example.test", password="password-must-not-leak",
        server="HEROFX", account_id=account_id, account_number="2",
        environment=environment,
    )


class Repository:
    def __init__(self, connections):
        self.connections = connections

    def get_connection(self, user):
        return self.connections.get(user)


class Client:
    def __init__(self, *, configs=None, state_payload=None, **kwargs):
        self.account_id = kwargs["account_id"]
        self.account_number = kwargs["account_number"]
        self.password = kwargs["password"]
        self.configs = list(configs or [config()])
        self.state_payload = state_payload or state()
        self.config_calls = 0
        self.token_refresh_count = 0
        self.closed = False

    async def get_accounts(self):
        return {"accounts": [{
            "accountId": self.account_id, "accNum": self.account_number,
            "name": "HEROFX#account#2", "currency": "USD", "status": "active",
        }]}

    async def get_config(self):
        index = min(self.config_calls, len(self.configs) - 1)
        self.config_calls += 1
        return self.configs[index]

    async def get_account_state_payload(self):
        return self.state_payload

    async def aclose(self):
        self.closed = True


def service_for(repo, client, cache=None):
    return TradeLockerAccountStatusService(
        repository=repo,
        cache=cache or TradeLockerConfigCache(),
        client_factory=lambda **kwargs: client,
    )


@pytest.mark.asyncio
async def test_normalizes_zero_balance_margin_and_integer_counts():
    selected = connection()
    client = Client(
        base_url=selected.base_url, username=selected.username, password=selected.password,
        server=selected.server, account_id=selected.account_id,
        account_number=selected.account_number,
    )
    result = await service_for(Repository({"user-a": selected}), client).retrieve("user-a")
    payload = result.model_dump(mode="json")
    assert payload["balance"] == 0.0
    assert payload["margin"]["warning_level"] == 100.0
    assert payload["margin"]["stop_out_level"] == 166.67
    assert payload["available_funds"] == 0.0
    assert isinstance(payload["positions_count"], int)
    assert isinstance(payload["pending_orders_count"], int)
    assert "accountDetailsData" not in str(payload)
    assert client.closed


@pytest.mark.asyncio
async def test_one_forced_config_refresh_after_length_mismatch():
    selected = connection()
    client = Client(
        configs=[config(FIELDS[:-1]), config()],
        base_url=selected.base_url, username=selected.username, password=selected.password,
        server=selected.server, account_id=selected.account_id,
        account_number=selected.account_number,
    )
    result = await service_for(Repository({"user-a": selected}), client).retrieve("user-a")
    assert result.balance == 0.0
    assert client.config_calls == 2


@pytest.mark.asyncio
async def test_second_mapping_mismatch_fails_closed_after_one_refresh():
    selected = connection()
    client = Client(
        configs=[config(FIELDS[:-1])],
        base_url=selected.base_url, username=selected.username, password=selected.password,
        server=selected.server, account_id=selected.account_id,
        account_number=selected.account_number,
    )
    with pytest.raises(AccountStatusUnavailable) as caught:
        await service_for(Repository({"user-a": selected}), client).retrieve("user-a")
    assert caught.value.code == "account_field_mapping_unavailable"
    assert client.config_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configs", "state_payload"),
    [([{"d": {}}], state()), ([config()], {"d": {}}), ([config()], state([*VALUES[:-1], "bad"]))],
)
async def test_missing_or_malformed_mapping_fails_closed(configs, state_payload):
    selected = connection()
    client = Client(
        configs=configs, state_payload=state_payload,
        base_url=selected.base_url, username=selected.username, password=selected.password,
        server=selected.server, account_id=selected.account_id,
        account_number=selected.account_number,
    )
    with pytest.raises(AccountStatusUnavailable) as caught:
        await service_for(Repository({"user-a": selected}), client).retrieve("user-a")
    assert caught.value.code == "account_field_mapping_unavailable"


def test_config_cache_isolates_users_environments_and_accounts():
    cache = TradeLockerConfigCache()
    keys = [
        TradeLockerConfigCacheKey("user-a", "demo", "HEROFX", "1", "2"),
        TradeLockerConfigCacheKey("user-b", "demo", "HEROFX", "1", "2"),
        TradeLockerConfigCacheKey("user-a", "live", "HEROFX", "1", "2"),
        TradeLockerConfigCacheKey("user-a", "demo", "HEROFX", "9", "2"),
    ]
    for index, key in enumerate(keys):
        cache.put(key, {"marker": index})
    assert [cache.get(key)["marker"] for key in keys] == [0, 1, 2, 3]
    cache.invalidate_user("user-a")
    assert cache.get(keys[0]) is None and cache.get(keys[1]) == {"marker": 1}


@pytest.mark.asyncio
async def test_each_user_resolves_only_their_selected_account():
    connections = {
        "user-a": connection(user="user-a", account_id="111"),
        "user-b": connection(user="user-b", account_id="222"),
    }
    observed = []

    def factory(**kwargs):
        observed.append(kwargs["account_id"])
        return Client(**kwargs)

    service = TradeLockerAccountStatusService(
        repository=Repository(connections), cache=TradeLockerConfigCache(),
        client_factory=factory,
    )
    first = await service.retrieve("user-a")
    second = await service.retrieve("user-b")
    assert first.account.account_id == "111"
    assert second.account.account_id == "222"
    assert observed == ["111", "222"]


@pytest.mark.asyncio
async def test_token_refresh_observation_does_not_change_selected_account(caplog):
    selected = connection(account_id="780896")
    client = Client(
        base_url=selected.base_url, username=selected.username, password=selected.password,
        server=selected.server, account_id=selected.account_id,
        account_number=selected.account_number,
    )
    client.token_refresh_count = 1
    with caplog.at_level(logging.INFO):
        result = await service_for(Repository({"user-a": selected}), client).retrieve("user-a")
    assert result.account.account_id == "780896"
    logs = caplog.text
    assert "token_refresh=True" in logs
    assert "password-must-not-leak" not in logs
    assert "Authorization" not in logs
