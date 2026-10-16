from __future__ import annotations

import re
from urllib.parse import unquote

from app.models.instruments import CanonicalInstrument


FOREX_NAMES = {
    "EUR/USD": "Euro / U.S. Dollar", "GBP/USD": "British Pound / U.S. Dollar",
    "USD/JPY": "U.S. Dollar / Japanese Yen", "USD/CHF": "U.S. Dollar / Swiss Franc",
    "AUD/USD": "Australian Dollar / U.S. Dollar", "USD/CAD": "U.S. Dollar / Canadian Dollar",
    "NZD/USD": "New Zealand Dollar / U.S. Dollar",
}
DEFAULT_FOREX_MAJORS = tuple(FOREX_NAMES)


class InstrumentMappingError(ValueError):
    pass


class InstrumentMapper:
    @staticmethod
    def canonical_id(asset_class: str, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if asset_class == "forex":
            compact = re.sub(r"[^A-Z]", "", normalized)
            if len(compact) != 6:
                raise InstrumentMappingError("A forex symbol must contain two three-letter currencies.")
            normalized = f"{compact[:3]}/{compact[3:]}"
        return f"{asset_class}:{normalized}"

    def resolve(self, canonical_id: str) -> CanonicalInstrument:
        decoded = unquote(canonical_id)
        if ":" not in decoded:
            raise InstrumentMappingError("A canonical instrument identifier is required.")
        asset_class, raw = decoded.split(":", 1)
        symbol = raw.upper()
        if asset_class == "forex":
            canonical_id = self.canonical_id("forex", symbol)
            symbol = canonical_id.split(":", 1)[1]
            compact = symbol.replace("/", "")
            return CanonicalInstrument(
                canonical_id=canonical_id, symbol=symbol, display_symbol=symbol,
                description=FOREX_NAMES.get(symbol, f"{symbol[:3]} / {symbol[4:]}"), asset_class="forex",
                provider_symbols={"tradingview": f"OANDA:{compact}", "finnhub": f"OANDA:{symbol.replace('/', '_')}"},
            )
        provider_symbols: dict[str, str] = {}
        if asset_class == "equity":
            provider_symbols = {"tradingview": symbol, "finnhub": symbol}
        elif asset_class == "index":
            provider_symbols = {"tradingview": symbol, "finnhub": symbol}
        elif asset_class == "metal":
            compact = symbol.replace("/", "")
            provider_symbols = {"tradingview": f"OANDA:{compact}", "finnhub": f"OANDA:{symbol.replace('/', '_')}"}
        elif asset_class == "crypto":
            compact = symbol.replace("/", "")
            provider_symbols = {"tradingview": f"COINBASE:{compact}", "finnhub": f"COINBASE:{symbol.replace('/', '')}"}
        return CanonicalInstrument(canonical_id=f"{asset_class}:{symbol}", symbol=symbol,
            display_symbol=symbol, description=symbol, asset_class=asset_class if asset_class in {
                "equity", "index", "metal", "crypto", "energy", "option"} else "unknown",
            provider_symbols=provider_symbols)

    def from_finnhub(self, row: dict) -> CanonicalInstrument | None:
        raw = str(row.get("symbol") or "").upper()
        display = str(row.get("display_symbol") or raw).upper()
        kind = str(row.get("type") or "").lower()
        if raw.startswith("OANDA:"):
            compact = raw.split(":", 1)[1].replace("_", "")
            if len(compact) == 6:
                instrument = self.resolve(self.canonical_id("forex", compact))
                return instrument.model_copy(update={"description": row.get("description") or instrument.description})
        asset = "equity" if any(value in kind for value in ("stock", "equity", "common")) else "unknown"
        if asset == "unknown":
            return None
        instrument = self.resolve(f"equity:{display}")
        symbols = {**instrument.provider_symbols, "finnhub": raw}
        return instrument.model_copy(update={"description": row.get("description") or display,
            "exchange": row.get("exchange"), "provider_symbols": symbols})


instrument_mapper = InstrumentMapper()
