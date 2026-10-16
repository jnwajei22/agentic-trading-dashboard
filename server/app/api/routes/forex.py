from fastapi import APIRouter

from app.models.api import (
    OrderPreviewRequest,
    OrderPreviewResponse,
)
from app.models.market import ForexPairConfig
from app.services.trading.previews import create_order_preview
from app.services.watchlist import get_default_watchlist


router = APIRouter(prefix="/forex", tags=["forex"])


@router.get("/watchlist", response_model=list[ForexPairConfig])
def forex_watchlist() -> list[ForexPairConfig]:
    return get_default_watchlist()


@router.post("/order-preview", response_model=OrderPreviewResponse)
def forex_order_preview(request: OrderPreviewRequest) -> OrderPreviewResponse:
    preview = create_order_preview(request)
    return OrderPreviewResponse.model_validate(preview, from_attributes=True)
