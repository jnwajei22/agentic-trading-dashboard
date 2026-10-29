import asyncio
from unittest.mock import AsyncMock

import pytest
from app.services.tradelocker.catalog_cache import InstrumentCatalogCache


@pytest.mark.asyncio
async def test_catalog_coalesces_account_reads_and_never_shares_across_accounts():
    cache=InstrumentCatalogCache(); release=asyncio.Event(); calls=[]
    async def load():
        calls.append(1); await release.wait(); return {"instruments":[{"id":1}]}
    a=asyncio.create_task(cache.get(("user","connection","account-a"),load))
    b=asyncio.create_task(cache.get(("user","connection","account-a"),load))
    await asyncio.sleep(0); release.set()
    left,right=await asyncio.gather(a,b)
    assert len(calls)==1
    left["instruments"][0]["id"]=99
    assert right["instruments"][0]["id"]==1
    other=AsyncMock(return_value={"instruments":[{"id":2}]})
    assert (await cache.get(("user","connection","account-b"),other))["instruments"][0]["id"]==2
    other.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalog_expires_invalidates_and_does_not_cache_failures():
    clock=[0];cache=InstrumentCatalogCache(ttl=10,maximum=1,clock=lambda:clock[0]);loader=AsyncMock(return_value={"instruments":[]})
    key=("user","connection","account")
    await cache.get(key,loader); await cache.get(key,loader); assert loader.await_count==1
    clock[0]=11;await cache.get(key,loader);assert loader.await_count==2
    cache.invalidate_user("user");await cache.get(key,loader);assert loader.await_count==3
    bad=AsyncMock(side_effect=RuntimeError("rate limited"))
    for _ in range(2):
        with pytest.raises(RuntimeError): await cache.get(("other",),bad)
    assert bad.await_count==2
