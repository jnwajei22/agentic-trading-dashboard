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
