from __future__ import annotations

from typing import Any


class TradeLockerMappingError(RuntimeError):
    """A safe failure raised when positional broker data cannot be verified."""

    def __init__(self, message: str, *, mismatch: bool = False) -> None:
        super().__init__(message)
        self.mismatch = mismatch


def configured_field_count(config_response: Any, config_key: str) -> int | None:
    try:
        columns = config_response["d"][config_key]["columns"]
    except (KeyError, TypeError):
        return None
    return len(columns) if isinstance(columns, list) else None


def positional_value_count(data_response: Any, data_key: str) -> int | None:
    try:
        values = data_response["d"][data_key]
    except (KeyError, TypeError):
        return None
    return len(values) if isinstance(values, list) else None


def map_configured_array(
    *,
    config_response: dict[str, Any],
    data_response: dict[str, Any],
    config_key: str,
    data_key: str,
) -> dict[str, Any]:
    try:
        columns = config_response["d"][config_key]["columns"]
        values = data_response["d"][data_key]
    except (KeyError, TypeError) as exc:
        raise TradeLockerMappingError(
            f"Malformed TradeLocker payload for {data_key}."
        ) from exc
    if not isinstance(columns, list) or not isinstance(values, list):
        raise TradeLockerMappingError(f"Malformed TradeLocker payload for {data_key}.")
    field_names: list[str] = []
    for column in columns:
        field_id = column.get("id") if isinstance(column, dict) else None
        if not isinstance(field_id, str) or not field_id.strip():
            raise TradeLockerMappingError(f"Malformed TradeLocker columns for {data_key}.")
        field_names.append(field_id)
    if len(set(field_names)) != len(field_names):
        raise TradeLockerMappingError(f"Duplicate TradeLocker columns for {data_key}.")
    if len(field_names) != len(values):
        raise TradeLockerMappingError(
            f"TradeLocker mapping mismatch for {data_key}: "
            f"{len(field_names)} fields but {len(values)} values.",
            mismatch=True,
        )
    return dict(zip(field_names, values, strict=True))


def map_configured_rows(
    *, config_response: dict[str, Any], data_response: dict[str, Any],
    config_key: str, data_key: str,
) -> list[dict[str, Any]]:
    """Map a TradeLocker row collection using its matching /trade/config columns."""
    try:
        columns = config_response["d"][config_key]["columns"]
        payload = data_response["d"]
        # Current TradeLocker reads return the positional collection directly in `d`.
        # Retain compatibility with the older named wrapper without ever exposing either raw form.
        rows = payload[data_key] if isinstance(payload, dict) else payload
    except (KeyError, TypeError) as exc:
        raise TradeLockerMappingError(f"Malformed TradeLocker payload for {data_key}.") from exc
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise TradeLockerMappingError(f"Malformed TradeLocker payload for {data_key}.")
    names = [column.get("id") if isinstance(column, dict) else None for column in columns]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise TradeLockerMappingError(f"Malformed TradeLocker columns for {data_key}.")
    mapped = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise TradeLockerMappingError(
                f"TradeLocker mapping mismatch for {data_key}.", mismatch=True
            )
        mapped.append(dict(zip(names, row, strict=True)))
    return mapped
