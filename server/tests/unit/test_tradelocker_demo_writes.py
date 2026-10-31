from __future__ import annotations

import pytest

from app.brokers.tradelocker.client import TradeLockerClient


@pytest.mark.asyncio
async def test_cancel_and_close_use_documented_scoped_routes():
    client=object.__new__(TradeLockerClient);client.account_number="7"
    calls=[]
    async def request(method,path,**kwargs):calls.append((method,path,kwargs));return {"ok":True}
    client._request=request
    await client.cancel_order("order-9")
    await client.close_position("position-4",strategy_id="afd-test")
    assert calls[0][0:2]==("DELETE","/trade/orders/order-9")
    assert calls[0][2]["headers"]["accNum"]=="7"
    assert calls[1][0:2]==("DELETE","/trade/positions/position-4")
    assert calls[1][2]["json"]=={"qty":0}
    assert calls[1][2]["params"]=={"strategyId":"afd-test"}


@pytest.mark.asyncio
async def test_position_stop_patch_preserves_take_profit_and_rejects_nonfinite_values():
    from app.brokers.tradelocker.client import TradeLockerError
    client=object.__new__(TradeLockerClient);client.account_number="7";calls=[]
    async def request(method,path,**kwargs):calls.append((method,path,kwargs));return {}
    client._request=request
    await client.update_position_stop("123",1.1,strategy_id="afd-protect")
    method,path,request=calls[0]
    assert method=="PATCH" and path=="/trade/positions/123"
    assert request["json"]=={"stopLoss":1.1} and request["headers"]["accNum"]=="7"
    with pytest.raises(TradeLockerError):await client.update_position_stop("123",float("nan"),strategy_id="invalid")
    assert len(calls)==1
