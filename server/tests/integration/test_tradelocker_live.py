import os

import pytest

from app.brokers.tradelocker.client import TradeLockerClient
from app.config.settings import settings


REAL_CONFIG = (
    settings.tradelocker_username,
    settings.tradelocker_password,
    settings.tradelocker_server,
    settings.tradelocker_account_id,
    settings.tradelocker_account_number,
)
RUN_LIVE = os.getenv("RUN_TRADELOCKER_INTEGRATION") == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not RUN_LIVE or not all(REAL_CONFIG),
    reason="set RUN_TRADELOCKER_INTEGRATION=1 with real credentials to run",
)
async def test_real_tradelocker_read_only_account_status():
    async with TradeLockerClient(
        base_url=settings.tradelocker_base_url,
        username=settings.tradelocker_username,
        password=settings.tradelocker_password,
        server=settings.tradelocker_server,
        account_id=settings.tradelocker_account_id,
        account_number=settings.tradelocker_account_number,
    ) as client:
        result = await client.get_account_state_payload()

    assert result is not None
