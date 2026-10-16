from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ChartType = Literal["candlestick", "line"]
LineStyleName = Literal["solid", "dashed", "dotted"]
_UNSAFE_TEXT = re.compile(
    r"[<>{}`]|(?:javascript|data|vbscript)\s*:|(?:https?|ftp)\s*://|url\s*\(|style\s*=|on\w+\s*=",
    re.IGNORECASE,
)


def _safe_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or _UNSAFE_TEXT.search(cleaned) or any(ord(char) < 32 for char in cleaned):
        raise ValueError("Text must be plain, non-empty, and contain no markup, code, or URL.")
    return cleaned


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Numeric values must be finite.")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("Timestamp must be an ISO-8601 UTC value ending in Z or +00:00.")
    return value.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HorizontalOverlay(StrictModel):
    type: Literal["horizontal_line"]
    label: str = Field(min_length=1, max_length=80)
    price: float
    line_style: LineStyleName = "solid"
    line_width: Literal[1, 2, 3] = 1

    _label = field_validator("label")(_safe_text)
    _price = field_validator("price")(_finite)


class LinePoint(StrictModel):
    timestamp: datetime
    value: float

    _timestamp = field_validator("timestamp")(_utc)
    _value = field_validator("value")(_finite)


class LineOverlay(StrictModel):
    type: Literal["line_series"]
    label: str = Field(min_length=1, max_length=80)
    points: list[LinePoint] = Field(min_length=1, max_length=2000)
    line_style: LineStyleName = "solid"
    line_width: Literal[1, 2, 3] = 1

    _label = field_validator("label")(_safe_text)


class ChartMarker(StrictModel):
    timestamp: datetime
    position: Literal["above", "below"]
    shape: Literal["circle", "square", "arrow_up", "arrow_down"]
    label: str = Field(min_length=1, max_length=80)

    _timestamp = field_validator("timestamp")(_utc)
    _label = field_validator("label")(_safe_text)


class RenderMarketChartRequest(StrictModel):
    series_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    chart_type: ChartType = "candlestick"
    title: str | None = Field(default=None, max_length=120)
    show_volume: bool = True
    horizontal_overlays: list[HorizontalOverlay] = Field(default_factory=list, max_length=30)
    line_overlays: list[LineOverlay] = Field(default_factory=list, max_length=10)
    markers: list[ChartMarker] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _safe_text(value) if value is not None else None
