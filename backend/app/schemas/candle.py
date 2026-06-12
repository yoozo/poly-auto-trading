from datetime import datetime
from typing import Literal

from pydantic import BaseModel


Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


class Candle(BaseModel):
    symbol: str
    interval: Interval
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


class BollingerBands(BaseModel):
    upper: float | None = None
    middle: float | None = None
    lower: float | None = None


class IndicatorPoint(BaseModel):
    symbol: str
    interval: Interval
    candle_time: datetime
    rsi: float | None = None
    rsi_ema: float | None = None
    rsi_ema_diff: float | None = None
    bollinger: BollingerBands = BollingerBands()
