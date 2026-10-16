from datetime import datetime

from pydantic import BaseModel

from app.models.enums import Direction, OrderPreviewStatus
from app.models.orders import OrderRequest


class OrderPreviewRequest(OrderRequest):
    pass


class OrderPreviewResponse(BaseModel):
    preview_id: str
    status: OrderPreviewStatus
    pair: str
    side: Direction
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float
    pip_risk: float
    risk_amount: float
    reward_risk: float
    violations: list[str]
    expires_at: datetime
