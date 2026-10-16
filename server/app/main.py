import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api.routes.health import router as health_router
from app.api.routes.forex import router as forex_router
from app.api.routes.platform import router as platform_router
from app.api.routes.agent import router as agent_router
from app.webhooks.tradingview import router as tradingview_router
from app.config.settings import settings
from app.storage.database import check_database, DatabaseUnavailable
from app.storage.brokers import BrokerRepository, BrokerStorageError
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository
from app.storage.runtime_settings import RuntimeSettingsRepository
from app.storage.signal_intents import SignalIntentRepository
from app.jobs.autonomous_scheduler import AutonomousSchedulerWorker

@asynccontextmanager
async def lifespan(app_instance):
    check_database()
    BrokerRepository(); ExecutionRepository(); ScheduleRepository()
    RuntimeSettingsRepository(); SignalIntentRepository()
    worker = AutonomousSchedulerWorker() if settings.autonomous_scheduler_embedded else None
    task = asyncio.create_task(worker.run_forever()) if worker else None
    try:
        yield
    finally:
        if worker:
            worker.stop()
            await task

app = FastAPI(title="Agentic Trading Desk", version="0.3.0", lifespan=lifespan)

@app.exception_handler(DatabaseUnavailable)
@app.exception_handler(BrokerStorageError)
async def broker_storage_error_handler(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

app.include_router(health_router)
app.include_router(forex_router)
app.include_router(platform_router)
app.include_router(agent_router)
app.include_router(tradingview_router, prefix="/webhooks", tags=["webhooks"])
