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


@pytest.mark.asyncio
async def test_scoped_quote_honors_read_cooldown_without_caching_prices():
    import httpx
    from app.brokers.tradelocker.client import TradeLockerClient
    from app.services.tradelocker.catalog_cache import instrument_catalog_cache
    instrument_catalog_cache.invalidate_user('quote-retry-test')
    calls=[]; cooldowns=[]
    class Limiter:
        def state(self, scope): return {"cooldown_epoch":0}
        async def acquire(self, scope): return scope
        def release(self, lease): pass
        def set_cooldown(self, scope, delay): cooldowns.append(delay)
    def route(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/auth/jwt/token'): return httpx.Response(200,json={'accessToken':'token'})
        if request.url.path.endswith('/instruments'): return httpx.Response(200,json={'d':{'instruments':[{'id':42,'name':'EURUSD','routes':[{'id':'r1','type':'INFO'}]}]}})
        count=sum(path.endswith('/quotes') for _,path in calls)
        if count==1: return httpx.Response(429,headers={'Retry-After':'2'})
        return httpx.Response(200,json={'d':{'bp':1.1+count/10000,'ap':1.1002+count/10000}})
    async with TradeLockerClient(base_url='https://demo.tradelocker.test/backend-api',username='u',password='p',server='s',
        account_id='a',account_number='1',transport=httpx.MockTransport(route),request_limiter=Limiter(),
        cache_user_id='quote-retry-test',cache_connection_id='connection',cache_account_record_id='account') as client:
        first=await client.get_quote('EURUSD'); second=await client.get_quote('EURUSD')
    assert first != second
    assert sum(path.endswith('/quotes') for _,path in calls)==3
    assert sum(path.endswith('/instruments') for _,path in calls)==1
    assert cooldowns == [2,1,1]
    assert all(method=='GET' or path.endswith('/auth/jwt/token') for method,path in calls)


@pytest.mark.asyncio
async def test_long_quote_cooldown_returns_immediately_without_waiting_or_requesting():
    import time
    from types import SimpleNamespace
    from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
    limiter=SimpleNamespace(state=lambda _: {'cooldown_epoch':time.time()+3600},acquire=AsyncMock())
    async with TradeLockerClient(base_url='https://demo.tradelocker.test/backend-api',username='u',password='p',server='s',
        account_id='a',account_number='1',request_limiter=limiter,
        cache_user_id='cooldown-test',cache_connection_id='connection',cache_account_record_id='account') as client:
        client._resolve_instrument=AsyncMock(return_value=(42,'route'))
        client._optional_get=AsyncMock()
        with pytest.raises(TradeLockerError) as error: await client.get_quote('EURUSD')
        assert error.value.code=='rate_limited' and error.value.details['retry_after_seconds'] > 3500
        limiter.acquire.assert_not_awaited(); client._optional_get.assert_not_awaited()
