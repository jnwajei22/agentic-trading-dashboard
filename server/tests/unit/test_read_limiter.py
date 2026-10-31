import asyncio
import time

import pytest

from app.config.settings import settings
from app.services.tradelocker.rate_limiter import TradeLockerRequestLimiter


@pytest.mark.asyncio
async def test_waiting_reader_observes_early_lease_release_instead_of_sleeping_until_crash_deadline(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'tradelocker_rate_limit_min_interval_seconds',0)
    limiter=TradeLockerRequestLimiter(tmp_path/'reads.db')
    first=await limiter.acquire('same-operation')
    started=time.monotonic()
    waiting=asyncio.create_task(limiter.acquire('same-operation'))
    await asyncio.sleep(.05)
    assert not waiting.done()
    limiter.release(first)
    second=await asyncio.wait_for(waiting,timeout=1)
    try:
        assert second.owner != first.owner
        assert time.monotonic()-started < 1.1
    finally:limiter.release(second)


@pytest.mark.asyncio
async def test_releasing_a_lease_does_not_clear_provider_cooldown(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'tradelocker_rate_limit_min_interval_seconds',0)
    limiter=TradeLockerRequestLimiter(tmp_path/'reads.db')
    lease=await limiter.acquire('operation')
    limiter.set_cooldown('operation',.5)
    limiter.release(lease)
    assert limiter.state('operation')['cooldown_epoch'] > time.time()
    waiting=asyncio.create_task(limiter.acquire('operation'))
    await asyncio.sleep(.05)
    assert not waiting.done()
    second=await asyncio.wait_for(waiting,timeout=1)
    limiter.release(second)
