from pathlib import Path

from app.services.market_data.mock_provider import load_mock_candles


FIXTURE = Path(__file__).parents[1] / "fixtures" / "mock_candles.json"


def test_load_mock_candles_parses_fixture():
    candles = load_mock_candles(FIXTURE)
    assert set(candles) == {"EUR/USD", "GBP/USD"}
    assert candles["EUR/USD"][0].timestamp < candles["EUR/USD"][-1].timestamp
