from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from pydantic import ValidationError

from app.models.market import Candle


def normalize_candle(candle: Mapping[str, Any]) -> Candle:
    """Normalize expanded or TradeLocker abbreviated fields exactly once."""
    values = {
        "timestamp": candle.get("timestamp", candle.get("t", candle.get("time"))),
        "open": candle.get("open", candle.get("o")),
        "high": candle.get("high", candle.get("h")),
        "low": candle.get("low", candle.get("l")),
        "close": candle.get("close", candle.get("c")),
        "volume": candle.get("volume", candle.get("v", 0.0)),
    }
    if values["volume"] is None:
        values["volume"] = 0.0
    timestamp = values["timestamp"]
    if isinstance(timestamp, str):
        try:
            values["timestamp"] = float(timestamp)
        except ValueError:
            pass
    for field in ("open", "high", "low", "close", "volume"):
        value = values[field]
        if isinstance(value, str):
            try:
                value = float(value)
                values[field] = value
            except ValueError:
                pass
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"Candle {field} must be numeric.")
    try:
        normalized = Candle.model_validate(values)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError("Malformed candle data.") from exc
    if normalized.high < max(normalized.open, normalized.close, normalized.low):
        raise ValueError("Candle high cannot be below open, close, or low.")
    if normalized.low > min(normalized.open, normalized.close, normalized.high):
        raise ValueError("Candle low cannot be above open, close, or high.")
    return normalized


def normalize_history_payload(payload: Any) -> tuple[list[Candle], int]:
    """Extract and normalize every supported TradeLocker history response shape."""
    if isinstance(payload, dict) and payload.get("status") == "not_implemented":
        return [], 0
    source = payload
    if isinstance(source, dict):
        source = source.get("d", source.get("data", source))
        if isinstance(source, dict) and "barDetails" in source:
            source = source["barDetails"]

    rows: list[Mapping[str, Any]] = []
    if isinstance(source, list):
        rows = [row for row in source if isinstance(row, Mapping)]
    elif isinstance(source, Mapping):
        aliases = {
            "timestamp": ("t", "timestamp", "time"),
            "open": ("o", "open"),
            "high": ("h", "high"),
            "low": ("l", "low"),
            "close": ("c", "close"),
            "volume": ("v", "volume"),
        }
        columns: dict[str, Any] = {}
        for canonical, names in aliases.items():
            columns[canonical] = next((source[name] for name in names if name in source), [])
        required = [columns[name] for name in ("timestamp", "open", "high", "low", "close")]
        if all(isinstance(column, list) for column in required):
            count = min(len(column) for column in required)
            rows = [
                {
                    name: column[index]
                    for name, column in columns.items()
                    if isinstance(column, list) and index < len(column)
                }
                for index in range(count)
            ]

    normalized: list[Candle] = []
    discarded = 0
    for row in rows:
        try:
            normalized.append(normalize_candle(row))
        except ValueError:
            discarded += 1
    return normalized, discarded
