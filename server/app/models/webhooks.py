from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Literal

class TradingViewSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["tradingview"]
    pair: str
    timeframe: str
    signal: str = Field(pattern="^(buy|sell|long|short|close)$")
    price: float = Field(gt=0)
    strategy: str = Field(min_length=1,max_length=100)
    timestamp: datetime
