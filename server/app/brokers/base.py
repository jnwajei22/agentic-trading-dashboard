from app.models.orders import OrderPreview

class BrokerAdapter:
    async def get_account(self) -> dict:
        raise NotImplementedError

    async def get_quote(self, pair: str) -> dict:
        raise NotImplementedError

    async def get_candles(self, pair: str, timeframe: str, lookback: int | None = 300, **kwargs):
        raise NotImplementedError

    async def get_open_positions(self) -> list[dict]:
        raise NotImplementedError

    async def preview_order(self, preview: OrderPreview) -> dict:
        raise NotImplementedError

    async def submit_order(self, preview: OrderPreview) -> dict:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> dict:
        raise NotImplementedError

    async def close_position(self, position_id: str) -> dict:
        raise NotImplementedError
