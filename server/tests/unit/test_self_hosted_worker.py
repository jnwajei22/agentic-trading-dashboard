from unittest.mock import AsyncMock

import pytest
from app.config.settings import settings
from app.services.autonomous.runner import AutonomousDecisionRunner
from scripts import run_worker


@pytest.mark.asyncio
async def test_shadow_worker_always_forces_dry_run(monkeypatch):
    call=AsyncMock(return_value={"status":"no_trade"})
    monkeypatch.setattr(AutonomousDecisionRunner, "run", call)
    # No repositories are needed to verify the dispatcher boundary.
    shadow=object.__new__(run_worker.ShadowRunner)
    await shadow.run("user", "profile", "key", "scheduled", allow_safe_retry=True)
    assert call.await_args.kwargs == {"allow_safe_retry":True, "dry_run":True}


@pytest.mark.parametrize("field,value",[("storage_backend","sqlite"),("live_trading_enabled",True),("autonomous_scheduler_embedded",True)])
def test_worker_rejects_unsafe_runtime_before_connecting(monkeypatch, field, value):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "storage_backend", "postgres")
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "autonomous_scheduler_embedded", False)
    monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(run_worker, "check_database", lambda: pytest.fail("must reject before database access"))
    with pytest.raises(RuntimeError): run_worker.preflight()
