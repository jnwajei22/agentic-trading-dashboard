from app.models.market import Candle

class MarketDataProvider:
    async def get_quote(self, pair: str) -> dict:
        raise NotImplementedError

    async def get_candles(self, pair: str, timeframe: str, lookback: int) -> list[Candle]:
        raise NotImplementedError
