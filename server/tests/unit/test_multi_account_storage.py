from __future__ import annotations

import pytest

from app.services.tradelocker.accounts import AccountResolutionError, BrokerAccountResolver
from app.storage.brokers import BrokerRepository, BrokerStorageError


def repo(tmp_path):
    return BrokerRepository(tmp_path / "multi.db", "secret")


def add_connection(storage, user, *, server="Alpha", environment="demo", create_new=False, select_default=True):
    connection = storage.save_connection(user, base_url=f"https://{environment}.tradelocker.test/backend-api",
        username=f"{user}@test", password="private", server=server, environment=environment,
        create_new=create_new)
    storage.sync_accounts(user, connection.connection_ref, {"accounts": [
        {"accountId": f"id-{server}", "accNum": "7", "name": f"{server} account", "currency": "USD"}
    ]})
    if select_default and not any(account["is_default_analysis"] for account in storage.list_accounts(user)):
        account = next(account for account in storage.list_accounts(user) if account["connection_id"] == connection.connection_ref)
        storage.set_default_account(user, account["public_id"])
    return connection


def test_discovery_persists_every_account_without_choosing_a_default(tmp_path):
    storage=repo(tmp_path)
    connection=storage.save_connection("new-user",base_url="https://demo.tradelocker.test/backend-api",
        username="new@test",password="private",server="Three",environment="demo")
    discovered={"accounts":[
        {"accountId":"a1","accNum":"101","name":"Primary demo","currency":"USD"},
        {"accountId":"a2","accNum":"102","name":"Secondary demo","currency":"EUR"},
        {"accountId":"a3","accNum":"103","name":"Third demo","currency":"GBP"},
    ]}
    storage.sync_accounts("new-user",connection.connection_ref,discovered)
    accounts=storage.list_accounts("new-user")
    assert len(accounts) == 3
    assert {account["account_name"] for account in accounts} == {"Primary demo","Secondary demo","Third demo"}
    assert not any(account["is_default_analysis"] for account in accounts)


def test_explicit_selection_chooses_exactly_one_and_rediscovery_preserves_it(tmp_path):
    storage=repo(tmp_path)
    connection=storage.save_connection("new-user",base_url="https://demo.tradelocker.test/backend-api",
        username="new@test",password="private",server="Three",environment="demo")
    discovered={"accounts":[
        {"accountId":"a1","accNum":"101","name":"Primary demo"},
        {"accountId":"a2","accNum":"102","name":"Chosen demo"},
        {"accountId":"a3","accNum":"103","name":"Third demo"},
    ]}
    storage.sync_accounts("new-user",connection.connection_ref,discovered)
    assert storage.select_account("new-user","a2","102",connection.connection_ref)
    selected=[account for account in storage.list_accounts("new-user") if account["is_default_analysis"]]
    assert len(selected) == 1
    assert selected[0]["account_name"] == "Chosen demo"
    assert len(storage.list_accounts("new-user")) == 3
    assert all(account["available"] for account in storage.list_accounts("new-user"))

    storage.sync_accounts("new-user",connection.connection_ref,{"accounts":[
        {"accountId":"a1","accNum":"101","name":"Primary renamed"},
        {"accountId":"a2","accNum":"102","name":"Chosen renamed"},
        {"accountId":"a3","accNum":"103","name":"Third renamed"},
    ]})
    selected=[account for account in storage.list_accounts("new-user") if account["is_default_analysis"]]
    assert len(selected) == 1
    assert selected[0]["account_name"] == "Chosen renamed"


def test_rediscovery_marks_missing_accounts_unavailable_without_deleting_them(tmp_path):
    storage=repo(tmp_path)
    connection=storage.save_connection("new-user",base_url="https://demo.tradelocker.test/backend-api",
        username="new@test",password="private",server="Three",environment="demo")
    storage.sync_accounts("new-user",connection.connection_ref,{"accounts":[
        {"accountId":"a1","accNum":"101"},{"accountId":"a2","accNum":"102"},{"accountId":"a3","accNum":"103"},
    ]})
    storage.sync_accounts("new-user",connection.connection_ref,{"accounts":[{"accountId":"a2","accNum":"102"}]})
    accounts=storage.list_accounts("new-user")
    assert len(accounts) == 3
    assert sum(account["available"] for account in accounts) == 1
    assert sum(not account["available"] for account in accounts) == 2


def test_multiple_connections_and_accounts_are_preserved(tmp_path):
    storage=repo(tmp_path); first=add_connection(storage,"u",server="Alpha")
    second=add_connection(storage,"u",server="Beta",environment="live",create_new=True)
    assert len(storage.list_connections("u")) == 2
    accounts=storage.list_accounts("u")
    assert len(accounts) == 2
    assert {a["environment"] for a in accounts} == {"demo","live"}
    assert first.connection_ref != second.connection_ref


def test_discovery_reconciles_without_changing_alias_or_default(tmp_path):
    storage=repo(tmp_path); connection=add_connection(storage,"u")
    original=storage.list_accounts("u")[0]
    storage.rename_account("u",original["public_id"],"Main-Demo")
    storage.sync_accounts("u",connection.connection_ref,{"accounts":[{"accountId":"id-Alpha","accNum":"7","name":"Renamed upstream"}]})
    current=storage.list_accounts("u")[0]
    assert current["account_alias"] == "main-demo"
    assert current["is_default_analysis"] is True


def test_missing_discovered_account_is_marked_unavailable(tmp_path):
    storage=repo(tmp_path); connection=add_connection(storage,"u")
    storage.sync_accounts("u",connection.connection_ref,{"accounts":[]})
    assert storage.list_accounts("u")[0]["available"] is False
    with pytest.raises(AccountResolutionError, match="unavailable"):
        BrokerAccountResolver(storage).resolve("u")


def test_alias_is_case_insensitive_and_unique_per_user(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"u")
    second=add_connection(storage,"u",server="Beta",create_new=True)
    accounts=storage.list_accounts("u")
    storage.rename_account("u",accounts[0]["public_id"],"Primary")
    with pytest.raises(BrokerStorageError, match="already in use"):
        storage.rename_account("u",accounts[1]["public_id"],"PRIMARY")
    context=BrokerAccountResolver(storage).resolve("u",account_alias="pRiMaRy")
    assert context.account_alias == "primary"
    assert second.connection_ref != context.connection_ref


def test_default_is_unique_and_changes_atomically(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"u"); add_connection(storage,"u",server="Beta",create_new=True)
    accounts=storage.list_accounts("u")
    assert sum(a["is_default_analysis"] for a in accounts) == 1
    target=next(a for a in accounts if not a["is_default_analysis"])
    assert storage.set_default_account("u",target["public_id"])
    assert BrokerAccountResolver(storage).resolve("u").account_alias == target["account_alias"]


def test_tenant_scope_blocks_foreign_ids_aliases_and_profiles(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"alice"); add_connection(storage,"bob",server="Beta")
    alice=storage.list_accounts("alice")[0]
    assert storage.rename_account("bob",alice["public_id"],"stolen") is False
    assert storage.set_default_account("bob",alice["public_id"]) is False
    with pytest.raises(AccountResolutionError):
        BrokerAccountResolver(storage).resolve("bob",account_alias=alice["account_alias"])


def test_profiles_bind_owned_account_and_default_read_only(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"u")
    account=storage.list_accounts("u")[0]
    profile=storage.create_profile("u",name="Hourly",account_ref=account["public_id"])
    assert profile["execution_mode"] == "read_only"
    context=BrokerAccountResolver(storage).resolve("u",profile=profile["public_id"])
    assert context.account_alias == account["account_alias"]
    assert storage.update_profile("u",profile["public_id"],execution_mode="disabled")
    assert storage.list_profiles("u")[0]["execution_mode"] == "disabled"
    assert storage.delete_profile("u",profile["public_id"])


def test_connection_reauthentication_preserves_account_identity(tmp_path):
    storage=repo(tmp_path); connection=add_connection(storage,"u")
    before=storage.list_accounts("u")[0]
    updated=storage.save_connection("u",connection_ref=connection.connection_ref,
        base_url=connection.base_url,username="new@test",password="new-private",server="Alpha")
    storage.sync_accounts("u",updated.connection_ref,{"accounts":[{"accountId":"id-Alpha","accNum":"7"}]})
    after=storage.list_accounts("u")[0]
    assert (after["public_id"],after["account_alias"]) == (before["public_id"],before["account_alias"])


def test_disabled_account_and_connection_fail_closed(tmp_path):
    storage=repo(tmp_path); connection=add_connection(storage,"u"); account=storage.list_accounts("u")[0]
    storage.set_account_enabled("u",account["public_id"],False)
    with pytest.raises(AccountResolutionError) as error: BrokerAccountResolver(storage).resolve("u")
    assert error.value.code == "account_disabled"
    storage.set_account_enabled("u",account["public_id"],True); storage.disable_connection("u",connection.connection_ref)
    with pytest.raises(AccountResolutionError) as error: BrokerAccountResolver(storage).resolve("u")
    assert error.value.code == "account_disabled"


def test_profile_resolution_ignores_live_dashboard_default(tmp_path):
    storage=repo(tmp_path)
    demo=add_connection(storage,"u",server="Demo",environment="demo")
    live=add_connection(storage,"u",server="Live",environment="live",create_new=True)
    accounts=storage.list_accounts("u")
    demo_account=next(a for a in accounts if a["connection_id"]==demo.connection_ref)
    live_account=next(a for a in accounts if a["connection_id"]==live.connection_ref)
    storage.set_default_account("u",live_account["public_id"])
    profile=storage.create_profile("u",name="Manual demo",account_ref=demo_account["public_id"],execution_mode="demo_manual")
    context=BrokerAccountResolver(storage).resolve("u",profile=profile["public_id"])
    assert context.account_record_id == demo_account["public_id"]
    assert context.environment == "demo"
    storage.set_default_account("u",demo_account["public_id"])
    storage.set_default_account("u",live_account["public_id"])
    assert BrokerAccountResolver(storage).resolve("u",profile=profile["public_id"]).account_record_id == demo_account["public_id"]


def test_account_listing_attaches_profiles_and_empty_lists(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"u"); add_connection(storage,"u",server="Beta",create_new=True)
    accounts=storage.list_accounts("u")
    profile=storage.create_profile("u",name="One profile",account_ref=accounts[0]["public_id"])
    listed={item["public_id"]:item for item in storage.list_accounts("u")}
    assert listed[accounts[0]["public_id"]]["profiles"][0]["public_id"] == profile["public_id"]
    assert listed[accounts[1]["public_id"]]["profiles"] == []


def test_explicit_safe_reference_overrides_default(tmp_path):
    storage=repo(tmp_path); add_connection(storage,"u"); add_connection(storage,"u",server="Beta",create_new=True)
    target=next(a for a in storage.list_accounts("u") if not a["is_default_analysis"])
    assert BrokerAccountResolver(storage).resolve("u",account_ref=target["public_id"]).account_alias == target["account_alias"]
