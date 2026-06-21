from datetime import datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"]


class Candle(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

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

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def finite_ohlcv(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("OHLCV fields must be finite numbers")
        return value

    @model_validator(mode="after")
    def valid_candle_shape(self) -> "Candle":
        if self.volume < 0:
            raise ValueError("volume must be greater than or equal to 0")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to open, close and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to open, close and high")
        if self.open_time >= self.close_time and self.volume != 0:
            raise ValueError("open_time must be before close_time unless the candle is a zero-volume placeholder")
        return self


class BollingerBands(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    upper: float | None = None
    middle: float | None = None
    lower: float | None = None

    @field_validator("upper", "middle", "lower")
    @classmethod
    def optional_finite_value(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("indicator fields must be finite numbers or null")
        return value


class IndicatorPoint(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    symbol: str
    interval: Interval
    candle_time: datetime
    rsi: float | None = None
    rsi_ema: float | None = None
    rsi_ema_diff: float | None = None
    bollinger: BollingerBands = BollingerBands()

    @field_validator("rsi", "rsi_ema", "rsi_ema_diff")
    @classmethod
    def optional_finite_value(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("indicator fields must be finite numbers or null")
        return value
