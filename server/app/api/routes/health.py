from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.storage.database import check_database, DatabaseUnavailable
from app.storage.schedules import ScheduleRepository

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/ready")
def ready():
    try:
        database = check_database()
    except DatabaseUnavailable:
        return JSONResponse(status_code=503, content={"status":"not_ready", "database":"unavailable"})
    if not settings.autonomous_scheduler_required_for_readiness:
        return {"status":"ready", "database": database["backend"]}
    scheduler=ScheduleRepository().worker_health()
    ready=scheduler["status"]=="healthy"
    return JSONResponse(status_code=200 if ready else 503,content={"status":"ready" if ready else "not_ready","scheduler":scheduler["status"]})

@router.get("/scheduler/health")
def scheduler_health():
    health=ScheduleRepository().worker_health()
    return {"status":health["status"],"worker_count":len(health["workers"]),
        "healthy_workers":sum(1 for item in health["workers"] if item["healthy"])}
