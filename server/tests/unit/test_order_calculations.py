from datetime import datetime, timedelta, timezone

import pytest

from app.services.trading.order_calculations import InstrumentTerms, calculate_order, forex_terms


def terms(symbol="EURUSD", **overrides):
    values={"symbol":symbol,"pip_size":.0001,"tick_size":.00001,"price_precision":5,
        "contract_size":100000,"quantity_unit":"lot","quantity_step":.01,"minimum_quantity":.01,
        "maximum_quantity":100,"minimum_stop_distance":.0001,"leverage":50,"quote_currency":"USD"}
    values.update(overrides);return InstrumentTerms(**values)


def calculation(**overrides):
    values={"terms":terms(),"side":"buy","quantity":.5,"bid":1.3452,"ask":1.34528,
        "quote_timestamp":datetime.now(timezone.utc),"quote_source":"TradeLocker",
        "stop_loss":{"mode":"price","value":1.34},"take_profit":{"mode":"reward_multiple","value":2},
        "account_currency":"USD","account_equity":100000,"quote_to_account_rate":1}
    values.update(overrides);return calculate_order(**values)


def test_normal_and_jpy_pip_sizes_use_pair_specific_fallbacks():
    assert forex_terms("EUR/USD",{}).pip_size==.0001
    assert forex_terms("USD/JPY",{}).pip_size==.01


def test_pip_value_scales_with_lot_quantity_and_currency_conversion():
    assert calculation(quantity=.5)["pip_value"]["value"]==5
    converted=calculation(quantity=1,quote_to_account_rate=.8,account_currency="EUR")
    assert converted["pip_value"]=={"value":8,"currency":"EUR"}


def test_buy_uses_ask_sell_uses_bid_and_protection_distances_are_authoritative():
    buy=calculation();sell=calculation(side="sell",stop_loss={"mode":"price","value":1.35},
        take_profit={"mode":"price","value":1.34})
    assert buy["entry_price"]==1.34528 and buy["entry_side"]=="Ask"
    assert sell["entry_price"]==1.3452 and sell["entry_side"]=="Bid"
    assert buy["stop_loss"]["distance_pips"]==pytest.approx(52.8)
    assert sell["take_profit"]["distance_pips"]==pytest.approx(52)


def test_price_pip_and_reward_multiple_modes_recalculate_correctly():
    price=calculation(take_profit={"mode":"price","value":1.35584})
    pips=calculation(stop_loss={"mode":"pips","value":40},take_profit={"mode":"pips","value":80})
    reward=calculation(stop_loss={"mode":"pips","value":40},take_profit={"mode":"reward_multiple","value":2})
    assert price["take_profit"]["price"]==1.35584
    assert pips["stop_loss"]["price"]==1.34128 and pips["take_profit"]["price"]==1.35328
    assert reward["take_profit"]["distance_pips"]==80 and reward["take_profit"]["reward_to_risk"]==2


def test_provider_step_precision_margin_risk_and_exposure_are_normalized():
    result=calculation(quantity=.5)
    assert result["quantity"]["step"]==.01 and result["price_precision"]==5
    assert result["stop_loss"]["estimated_loss"]==264
    assert result["stop_loss"]["risk_percent"]==pytest.approx(.26)
    assert result["take_profit"]["estimated_profit"]==528
    assert result["notional_exposure"]==67264 and result["margin_estimate"]==1345.28


def test_missing_or_stale_quote_fails_closed_without_fake_zeroes():
    missing=calculation(bid=None,ask=None)
    stale=calculation(quote_timestamp=datetime.now(timezone.utc)-timedelta(minutes=2))
    assert missing["entry_price"] is None and missing["pip_value"] is None
    assert missing["notional_exposure"] is None and missing["blocking_reasons"]
    assert stale["quote"]["stale"] is True


def test_invalid_side_protection_and_quantity_are_blocking():
    wrong=calculation(stop_loss={"mode":"price","value":1.35},take_profit={"mode":"price","value":1.34})
    assert "below the entry price for a Buy order" in " ".join(wrong["blocking_reasons"])
    step=calculation(quantity=.505)
    assert step["entry_price"] is None and "provider minimum" in step["blocking_reasons"][0]
