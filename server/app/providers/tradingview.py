from __future__ import annotations

from app.services.instruments import instrument_mapper


class TradingViewChartProvider:
    provider_type = "tradingview_chart"

    def symbol_mapping(self, canonical_id: str) -> str:
        symbol = instrument_mapper.resolve(canonical_id).provider_symbols.get("tradingview")
        if not symbol: raise ValueError("TradingView has no approved mapping for this instrument.")
        return symbol

    def chart_configuration(self, canonical_id: str) -> dict:
        return {"provider_type":self.provider_type,"symbol":self.symbol_mapping(canonical_id),
            "execution_authoritative":False,"interval":"60","theme":"dark"}

    def datafeed_metadata(self) -> dict:
        return {"provider_type":self.provider_type,"role":"chart","execution_authoritative":False}


class TradingViewSignalProvider:
    provider_type = "tradingview_signal"

    def create_trade_intent(self, signal: dict) -> dict:
        return {"provider_type":self.provider_type,"signal":signal,"status":"pending_validation",
            "can_submit_order":False}
