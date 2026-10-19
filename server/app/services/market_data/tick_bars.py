"""Aggregate actual provider price events, never REST samples disguised as ticks.

Adapters must supply provider timestamps in milliseconds and an explicit candle
basis. Historical bars may only be merged when source, instrument and basis match.
No broker access, polling, trading actions or fake volume are implemented here.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

PriceBasis = Literal["BID", "ASK", "TRADE"]


@dataclass(frozen=True)
class PriceTick:
    source: str
    instrument: str
    timestamp_ms: int
    bid: float | None = None
    ask: float | None = None
    trade: float | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class TickBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    source: str
    instrument: str
    price_basis: PriceBasis
    partial: bool


@dataclass(frozen=True)
class BarUpdate:
    current: TickBar | None
    closed: TickBar | None = None
    accepted: bool = True
    reason: str | None = None


class TickBarBuilder:
    """One subscription with bounded memory, UTC bins and immutable updates.

    Initial and disconnected bars are partial. Continuous coverage begins with the
    next bar. Quiet periods produce no artificial candles. Late events may update
    the current bar's open/extremes but cannot rewind its close or edit closed bars.
    """
    def __init__(self, source: str, instrument: str, interval_ms: int, price_basis: PriceBasis):
        if not source.strip() or not instrument.strip():
            raise ValueError("Source and instrument are required.")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
            raise ValueError("A positive interval in milliseconds is required.")
        if price_basis not in {"BID", "ASK", "TRADE"}:
            raise ValueError("An explicit BID, ASK or TRADE candle basis is required.")
        self.source, self.instrument = source, instrument
        self.interval_ms, self.price_basis = interval_ms, price_basis
        self.current: TickBar | None = None
        self._first_tick_ms = self._last_tick_ms = 0
        self._gap = True
        self._seen: OrderedDict[str, None] = OrderedDict()

    def disconnected(self) -> None:
        """Call on disconnect, sequence gap or provider-declared data loss."""
        self._gap = True
        if self.current is not None:
            self.current = replace(self.current, partial=True)

    def push(self, tick: PriceTick) -> BarUpdate:
        if (tick.source, tick.instrument) != (self.source, self.instrument):
            return BarUpdate(self.current, accepted=False, reason="wrong_subscription")
        if isinstance(tick.timestamp_ms, bool) or not isinstance(tick.timestamp_ms, int) or tick.timestamp_ms <= 0:
            return BarUpdate(self.current, accepted=False, reason="invalid_timestamp")
        for value in (tick.bid, tick.ask, tick.trade):
            if value is not None and (isinstance(value, bool) or not isfinite(value) or value <= 0):
                return BarUpdate(self.current, accepted=False, reason="invalid_price")
        if tick.bid is not None and tick.ask is not None and tick.bid > tick.ask:
            return BarUpdate(self.current, accepted=False, reason="crossed_quote")
        price = {"BID": tick.bid, "ASK": tick.ask, "TRADE": tick.trade}[self.price_basis]
        if price is None:
            return BarUpdate(self.current, accepted=False, reason="missing_price_basis")
        timestamp = tick.timestamp_ms // self.interval_ms * self.interval_ms
        if self.current is not None and timestamp < self.current.timestamp:
            return BarUpdate(self.current, accepted=False, reason="closed_bar_event")
        if tick.event_id is not None:
            if tick.event_id in self._seen:
                return BarUpdate(self.current, accepted=False, reason="duplicate_event")
            self._seen[tick.event_id] = None
            if len(self._seen) > 1024:
                self._seen.popitem(last=False)
        closed = None
        if self.current is None or timestamp > self.current.timestamp:
            closed = self.current
            self.current = TickBar(timestamp, price, price, price, price, self.source,
                                   self.instrument, self.price_basis, partial=self._gap)
            self._first_tick_ms = self._last_tick_ms = tick.timestamp_ms
        else:
            self.current = replace(self.current,
                open=price if tick.timestamp_ms < self._first_tick_ms else self.current.open,
                high=max(self.current.high, price), low=min(self.current.low, price),
                close=price if tick.timestamp_ms >= self._last_tick_ms else self.current.close,
                partial=self.current.partial or self._gap)
            self._first_tick_ms = min(self._first_tick_ms, tick.timestamp_ms)
            self._last_tick_ms = max(self._last_tick_ms, tick.timestamp_ms)
        self._gap = False
        return BarUpdate(self.current, closed=closed)
