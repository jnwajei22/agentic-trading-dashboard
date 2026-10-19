from dataclasses import replace
import pytest
from app.services.market_data.tick_bars import PriceTick, TickBarBuilder

START = 1_800_000_000_000


def tick(offset, bid=1.1, **kwargs):
    return PriceTick("broker", "EURUSD", START + offset, bid=bid, **kwargs)


def builder(basis="BID"):
    return TickBarBuilder("broker", "EURUSD", 60_000, basis)


def test_events_update_ohlc_and_roll_at_exact_boundary():
    feed = builder()
    first = feed.push(tick(1000)).current
    feed.push(tick(2000, 1.2)); feed.push(tick(3000, 1.0))
    last = feed.push(tick(4000, 1.15)).current
    assert (last.open, last.high, last.low, last.close) == (1.1, 1.2, 1, 1.15)
    assert first.close == 1.1
    result = feed.push(tick(60_000, 1.3))
    assert result.closed == last and result.closed.partial
    assert result.current.timestamp == START + 60_000
    assert result.current.open == result.current.close == 1.3
    assert not result.current.partial


@pytest.mark.parametrize("basis,expected", [("BID",1.1),("ASK",1.2),("TRADE",1.15)])
def test_explicit_basis_not_implicit_midpoint(basis, expected):
    bar = builder(basis).push(tick(1000, ask=1.2, trade=1.15)).current
    assert bar.close == expected and bar.price_basis == basis
    assert not hasattr(bar, "volume")


def test_late_ticks_update_extremes_without_rewinding_close():
    feed = builder()
    feed.push(tick(2000, 1.1)); feed.push(tick(5000, 1.2))
    bar = feed.push(tick(1000, 0.9)).current
    assert (bar.open, bar.high, bar.low, bar.close) == (.9, 1.2, .9, 1.2)
    feed.push(tick(60_000))
    rejected = feed.push(tick(4000, 1.5))
    assert not rejected.accepted and rejected.reason == "closed_bar_event"
    assert rejected.current.timestamp == START + 60_000


def test_disconnect_flags_incomplete_coverage_without_filling_missing_minutes():
    feed = builder()
    feed.push(tick(0)); feed.push(tick(60_000))
    assert not feed.current.partial
    feed.disconnected()
    resumed = feed.push(tick(240_000, 1.2))
    assert resumed.closed.partial and resumed.current.partial
    assert resumed.current.timestamp == START + 240_000
    assert not feed.push(tick(300_000)).current.partial


def test_disconnect_within_bar_keeps_it_partial():
    feed = builder()
    feed.push(tick(0)); feed.push(tick(60_000)); feed.disconnected()
    assert feed.push(tick(61_000)).current.partial
    assert not feed.push(tick(120_000)).current.partial


def test_event_ids_dedupe_but_same_timestamp_updates_are_preserved():
    feed = builder()
    feed.push(tick(1000, event_id="one"))
    assert not feed.push(tick(1000, 1.3, event_id="one")).accepted
    assert feed.push(tick(1000, 1.2, event_id="two")).current.close == 1.2
    for i in range(1100): feed.push(tick(2000+i, event_id=str(i)))
    assert len(feed._seen) == 1024


@pytest.mark.parametrize("bad,reason", [
    (replace(tick(0), source="other"), "wrong_subscription"),
    (replace(tick(0), instrument="GBPUSD"), "wrong_subscription"),
    (replace(tick(0), timestamp_ms=True), "invalid_timestamp"),
    (replace(tick(0), timestamp_ms=0), "invalid_timestamp"),
    (tick(0, float("nan")), "invalid_price"),
    (tick(0, float("inf")), "invalid_price"),
    (tick(0, -1), "invalid_price"),
    (tick(0, True), "invalid_price"),
    (tick(0, 1.2, ask=1.1), "crossed_quote"),
    (tick(0, None, ask=1.2), "missing_price_basis"),
])
def test_invalid_events_cannot_change_chart(bad, reason):
    feed = builder()
    before = feed.push(tick(0)).current
    result = feed.push(bad)
    assert not result.accepted and result.reason == reason and result.current == before
