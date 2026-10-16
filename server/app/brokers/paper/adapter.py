from app.brokers.base import BrokerAdapter
from app.models.orders import OrderPreview

class PaperBrokerAdapter(BrokerAdapter):
    async def get_account(self) -> dict:
        return {"environment": "paper", "balance": 1000.0}

    async def submit_order(self, preview: OrderPreview) -> dict:
        return {
            "status": "paper_submitted",
            "paper_order_id": f"paper_{preview.preview_id}",
            "pair": preview.pair,
            "side": preview.side,
            "lot_size": preview.lot_size,
        }
