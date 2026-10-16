from __future__ import annotations

import pytest

from app.config.settings import settings
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository


class ActionClient:
    def __init__(self):
        self.orders=[["order-1"]];self.positions=[["position-1"]];self.cancel_calls=0;self.close_calls=0
    async def get_accounts(self):return {"accounts":[{"accountId":"a1","accNum":"7","name":"Demo","currency":"USD"}]}
    async def get_config(self):return {"d":{"ordersConfig":{"columns":[{"id":"id"}]},"positionsConfig":{"columns":[{"id":"id"}]}}}
    async def get_orders(self):return {"d":{"orders":self.orders}}
    async def get_open_positions(self):return {"d":{"positions":self.positions}}
    async def cancel_order(self,order_id):self.cancel_calls+=1;self.orders=[];return {"accepted":True}
    async def close_position(self,position_id,*,strategy_id):self.close_calls+=1;self.positions=[];return {"accepted":True}
    async def aclose(self):pass


def configured(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"tradelocker_demo_base_url","https://demo.tradelocker.test/backend-api")
    brokers=BrokerRepository(tmp_path/"app.db","secret")
    connection=brokers.save_connection("user",base_url=settings.tradelocker_demo_base_url,username="u",password="p",server="HeroFX",environment="demo")
    brokers.sync_accounts("user",connection.connection_ref,{"accounts":[{"accountId":"a1","accNum":"7"}]})
    account=brokers.list_accounts("user")[0]
    profile=brokers.create_profile("user",name="Manual",account_ref=account["public_id"],execution_mode="demo_manual")
    client=ActionClient()
    service=AutonomousDemoService(broker_repository=brokers,execution_repository=ExecutionRepository(tmp_path/"app.db"),client_factory=lambda **kwargs:client)
    return service,profile["public_id"],client


@pytest.mark.asyncio
@pytest.mark.parametrize("action,target",[("cancel_order","order-1"),("close_position","position-1")])
async def test_action_preview_submission_reconciliation_and_idempotency(tmp_path,monkeypatch,action,target):
    service,profile,client=configured(tmp_path,monkeypatch)
    preview=await service.review_action("user",profile,action,target)
    first=await service.submit_action("user",preview["preview_id"],"idempotency-123")
    second=await service.submit_action("user",preview["preview_id"],"idempotency-123")
    assert first["status"]=="verified" and first["reconciliation"]["target_absent"] is True
    assert second["execution_id"]==first["execution_id"]
    assert (client.cancel_calls if action=="cancel_order" else client.close_calls)==1


@pytest.mark.asyncio
async def test_action_preview_is_tenant_scoped(tmp_path,monkeypatch):
    service,profile,_=configured(tmp_path,monkeypatch)
    preview=await service.review_action("user",profile,"cancel_order","order-1")
    with pytest.raises(AutonomousExecutionError) as error:
        await service.submit_action("other-user",preview["preview_id"],"idempotency-123")
    assert error.value.code=="preview_not_found"


@pytest.mark.asyncio
async def test_kill_switch_does_not_block_validated_risk_reduction(tmp_path,monkeypatch):
    service,profile,_=configured(tmp_path,monkeypatch);monkeypatch.setattr(settings,"kill_switch_enabled",True)
    preview=await service.review_action("user",profile,"cancel_order","order-1")
    assert (await service.submit_action("user",preview["preview_id"],"idempotency-123"))["status"]=="verified"
