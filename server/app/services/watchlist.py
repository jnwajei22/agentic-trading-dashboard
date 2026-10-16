from app.models.market import ForexPairConfig

DEFAULT_FOREX_WATCHLIST = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
    "EUR/JPY",
    "GBP/JPY",
]

def get_default_watchlist() -> list[ForexPairConfig]:
    return [ForexPairConfig(pair=pair) for pair in DEFAULT_FOREX_WATCHLIST]

def normalize_pair(pair: str) -> str:
    cleaned = pair.upper().replace("/", "")
    if len(cleaned) == 6:
        return f"{cleaned[:3]}/{cleaned[3:]}"
    return pair.upper()

def is_allowed_pair(pair: str) -> bool:
    return normalize_pair(pair) in DEFAULT_FOREX_WATCHLIST
