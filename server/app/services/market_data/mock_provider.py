import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.models.market import Candle
from app.services.market_data.candles import normalize_candle

DEFAULT_MOCK_CANDLE_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mock_candles.json"
)


def parse_mock_candles(
    payload: Mapping[str, Sequence[Candle | dict]],
) -> dict[str, list[Candle]]:
    """Validate mocked candle mappings and order every series chronologically."""
    parsed: dict[str, list[Candle]] = {}
    for pair, values in payload.items():
        candles = [
            value if isinstance(value, Candle) else normalize_candle(value)
            for value in values
        ]
        parsed[pair] = sorted(candles, key=lambda candle: candle.timestamp)
    return parsed


def load_mock_candles(path: str | Path) -> dict[str, list[Candle]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mock candle JSON must contain a pair-to-candles object.")
    return parse_mock_candles(payload)
