from pydantic import BaseModel
from datetime import datetime
from app.models.enums import Direction, OrderPreviewStatus

class OrderRequest(BaseModel):
    pair: str
    side: Direction
    order_type: str = "market"
    entry: float
    stop_loss: float
    take_profit: float
    risk_percent: float = 0.5

class OrderPreview(BaseModel):
    preview_id: str
    status: OrderPreviewStatus = OrderPreviewStatus.preview_only
    pair: str
    side: Direction
    order_type: str
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float
    pip_risk: float
    risk_amount: float
    reward_risk: float
    violations: list[str] = []
    expires_at: datetime
    created_at: datetime
