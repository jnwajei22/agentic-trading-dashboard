from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class InstrumentTerms:
    symbol: str
    pip_size: float
    tick_size: float
    price_precision: int
    contract_size: float
    quantity_unit: str
    quantity_step: float
    minimum_quantity: float
    maximum_quantity: float | None = None
    minimum_stop_distance: float | None = None
    leverage: float | None = None
    margin_rate: float | None = None
    quote_currency: str | None = None


def forex_terms(symbol: str, metadata: dict[str, Any]) -> InstrumentTerms:
    pair = "".join(character for character in symbol.upper() if character.isalpha())
    if len(pair) != 6:
        raise ValueError("Only provider-identified forex instruments are currently calculable.")
    jpy = pair.endswith("JPY")
    pip = _positive(metadata.get("pip_size")) or (0.01 if jpy else 0.0001)
    precision = _integer(metadata.get("price_precision"))
    tick = _positive(metadata.get("tick_size")) or (10 ** -precision if precision is not None else pip / 10)
    precision = precision if precision is not None else max(0, -Decimal(str(tick)).normalize().as_tuple().exponent)
    return InstrumentTerms(
        symbol=pair, pip_size=pip, tick_size=tick, price_precision=precision,
        contract_size=_positive(metadata.get("contract_size")) or 100_000.0,
        quantity_unit="lot", quantity_step=_positive(metadata.get("quantity_step")) or 0.01,
        minimum_quantity=_positive(metadata.get("minimum_quantity")) or 0.01,
        maximum_quantity=_positive(metadata.get("maximum_quantity")),
        minimum_stop_distance=_positive(metadata.get("minimum_stop_distance")),
        leverage=_positive(metadata.get("leverage")), margin_rate=_positive(metadata.get("margin_rate")),
        quote_currency=str(metadata.get("quote_currency") or pair[3:]).upper(),
    )


def calculate_order(*, terms: InstrumentTerms, side: str, quantity: float, bid: float | None,
                    ask: float | None, quote_timestamp: datetime | None, quote_source: str,
                    stop_loss: dict[str, Any] | None, take_profit: dict[str, Any] | None,
                    account_currency: str | None, account_equity: float | None,
                    quote_to_account_rate: float | None) -> dict[str, Any]:
    side = side.lower()
    blocking: list[str] = []
    if side not in {"buy", "sell"}: blocking.append("Select Buy or Sell.")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        blocking.append("An authoritative Bid and Ask quote are required.")
    if not _valid_step(quantity, terms): blocking.append("Quantity does not match the provider minimum, maximum, or step.")
    if blocking:
        return _empty(terms, quantity, account_currency, quote_source, quote_timestamp, bid, ask, blocking)
    entry = ask if side == "buy" else bid
    assert entry is not None
    stop = _derived_price(stop_loss, entry, terms.pip_size, side, protective=True)
    stop_error = _protection_error("Stop Loss", stop, entry, side, protective=True, minimum=terms.minimum_stop_distance)
    stop_pips = abs(entry - stop) / terms.pip_size if stop is not None and not stop_error else None
    target = _target_price(take_profit, entry, terms.pip_size, side, stop_pips)
    target_error = _protection_error("Take Profit", target, entry, side, protective=False, minimum=terms.minimum_stop_distance)
    target_pips = abs(target - entry) / terms.pip_size if target is not None and not target_error else None
    rate = quote_to_account_rate if quote_to_account_rate and quote_to_account_rate > 0 else None
    pip_value = terms.contract_size * terms.pip_size * quantity * rate if rate else None
    loss = stop_pips * pip_value if stop_pips is not None and pip_value is not None else None
    profit = target_pips * pip_value if target_pips is not None and pip_value is not None else None
    risk_percent = loss / account_equity * 100 if loss is not None and account_equity and account_equity > 0 else None
    reward_risk = profit / loss if profit is not None and loss and loss > 0 else None
    exposure = terms.contract_size * quantity * entry * rate if rate else None
    margin = (exposure / terms.leverage if exposure is not None and terms.leverage else
              exposure * terms.margin_rate if exposure is not None and terms.margin_rate else None)
    now = datetime.now(timezone.utc)
    stale = quote_timestamp is None or (now - _aware(quote_timestamp)).total_seconds() > 30
    warnings = [message for message in (stop_error, target_error) if message]
    if rate is None: warnings.append("Account-currency conversion is unavailable; monetary estimates are omitted.")
    return {
        "quote": {"bid": bid, "ask": ask, "spread": ask - bid, "timestamp": quote_timestamp.isoformat() if quote_timestamp else None,
                  "source": quote_source, "stale": stale},
        "entry_price": _round(entry, terms.price_precision), "entry_side": "Ask" if side == "buy" else "Bid",
        "quantity": {"value": quantity, "unit": terms.quantity_unit, "minimum": terms.minimum_quantity,
                     "maximum": terms.maximum_quantity, "step": terms.quantity_step},
        "pip_size": terms.pip_size, "tick_size": terms.tick_size, "price_precision": terms.price_precision,
        "contract_size": terms.contract_size,
        "pip_value": {"value": _money(pip_value), "currency": account_currency} if pip_value is not None else None,
        "stop_loss": {"price": _round(stop, terms.price_precision), "distance_pips": _measure(stop_pips),
                      "estimated_loss": _money(loss), "risk_percent": _measure(risk_percent)} if stop is not None else None,
        "take_profit": {"price": _round(target, terms.price_precision), "distance_pips": _measure(target_pips),
                        "estimated_profit": _money(profit), "reward_to_risk": _measure(reward_risk)} if target is not None else None,
        "notional_exposure": _money(exposure), "margin_estimate": _money(margin),
        "warnings": warnings, "blocking_reasons": warnings,
    }


def _empty(terms: InstrumentTerms, quantity: float, currency: str | None, source: str,
           timestamp: datetime | None, bid: float | None, ask: float | None, blocking: list[str]) -> dict[str, Any]:
    return {"quote": {"bid": bid, "ask": ask, "spread": ask - bid if bid is not None and ask is not None else None,
                      "timestamp": timestamp.isoformat() if timestamp else None, "source": source, "stale": True},
            "entry_price": None, "entry_side": None,
            "quantity": {"value": quantity, "unit": terms.quantity_unit, "minimum": terms.minimum_quantity,
                         "maximum": terms.maximum_quantity, "step": terms.quantity_step},
            "pip_size": terms.pip_size, "tick_size": terms.tick_size, "price_precision": terms.price_precision,
            "contract_size": terms.contract_size, "pip_value": None, "stop_loss": None, "take_profit": None,
            "notional_exposure": None, "margin_estimate": None, "warnings": [], "blocking_reasons": blocking}


def _derived_price(request: dict[str, Any] | None, entry: float, pip: float, side: str, *, protective: bool) -> float | None:
    if not request or request.get("value") is None:return None
    value=float(request["value"]);mode=request.get("mode","price")
    if mode == "price":return value
    if mode != "pips" or value <= 0:return None
    direction = -1 if (side == "buy") == protective else 1
    return entry + direction * value * pip


def _target_price(request: dict[str, Any] | None, entry: float, pip: float, side: str, stop_pips: float | None) -> float | None:
    if not request or request.get("value") is None:return None
    if request.get("mode") in {"reward_multiple", "rr"}:
        if stop_pips is None:return None
        request={"mode":"pips","value":stop_pips*float(request["value"])}
    return _derived_price(request,entry,pip,side,protective=False)


def _protection_error(label: str, price: float | None, entry: float, side: str, *, protective: bool,
                      minimum: float | None) -> str | None:
    if price is None:return None
    valid = price < entry if (side == "buy") == protective else price > entry
    if not valid:return f"{label} must be {'below' if (side == 'buy') == protective else 'above'} the entry price for a {side.title()} order."
    if minimum and abs(entry-price) < minimum:return f"{label} does not meet the provider minimum distance."
    return None


def _valid_step(value: float, terms: InstrumentTerms) -> bool:
    if value < terms.minimum_quantity or (terms.maximum_quantity is not None and value > terms.maximum_quantity):return False
    steps=(Decimal(str(value))-Decimal(str(terms.minimum_quantity)))/Decimal(str(terms.quantity_step))
    return steps == steps.to_integral_value()


def _positive(value: Any) -> float | None:
    try:number=float(value)
    except (TypeError,ValueError):return None
    return number if number > 0 else None


def _integer(value: Any) -> int | None:
    try:number=int(value)
    except (TypeError,ValueError):return None
    return number if 0 <= number <= 10 else None


def _round(value: float | None, precision: int) -> float | None:
    if value is None:return None
    quantum=Decimal(1).scaleb(-precision)
    return float(Decimal(str(value)).quantize(quantum,rounding=ROUND_HALF_UP))


def _money(value: float | None) -> float | None:return round(value,2) if value is not None else None
def _measure(value: float | None) -> float | None:return round(value,2) if value is not None else None
def _aware(value: datetime) -> datetime:return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
