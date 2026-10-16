from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from typing import Any


MARKET_GROUPS = ("forex_majors", "forex_minors", "forex_exotics", "metals", "energies", "indices", "crypto", "equities", "other")
MAJOR_FX = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}
MAJOR_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}


def _value(row: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    return next((lowered[name.lower()] for name in names if lowered.get(name.lower()) is not None), None)


def classify_market_group(symbol: str, asset_class: str | None = None) -> tuple[str, str]:
    normalized = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    kind = (asset_class or "").lower()
    if kind in {"crypto", "cryptocurrency"} or any(token in normalized for token in ("BTC", "ETH", "SOL", "XRP")):
        return "crypto", "broker_metadata" if kind else "symbol_fallback"
    if kind in {"metal", "metals"} or normalized.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metals", "broker_metadata" if kind else "symbol_fallback"
    if kind in {"energy", "energies"} or any(token in normalized for token in ("WTI", "BRENT", "NGAS", "USOIL", "UKOIL")):
        return "energies", "broker_metadata" if kind else "symbol_fallback"
    if kind in {"index", "indices"}: return "indices", "broker_metadata"
    if kind in {"stock", "equity", "equities"}: return "equities", "broker_metadata"
    if kind in {"forex", "fx", "currency"} or (len(normalized) == 6 and normalized.isalpha()):
        if normalized in MAJOR_FX: return "forex_majors", "broker_metadata" if kind else "symbol_fallback"
        base, quote = normalized[:3], normalized[3:]
        group = "forex_minors" if base in MAJOR_CURRENCIES and quote in MAJOR_CURRENCIES else "forex_exotics"
        return group, "broker_metadata" if kind else "symbol_fallback"
    if any(token in normalized for token in ("US30", "NAS", "SPX", "GER", "UK100", "JP225")):
        return "indices", "symbol_fallback"
    return "other", "broker_metadata" if kind else "symbol_fallback"


def normalize_instrument(row: dict[str, Any]) -> dict[str, Any] | None:
    instrument_id = _value(row, "tradableInstrumentId", "instrumentId", "id")
    symbol = str(_value(row, "symbol", "name", "displaySymbol") or "").strip()
    if instrument_id is None or not symbol: return None
    asset_class = str(_value(row, "assetClass", "type", "category") or "other").lower()
    group, source = classify_market_group(symbol, asset_class)
    routes = row.get("routes") if isinstance(row.get("routes"), list) else []
    tradable_value = _value(row, "tradable", "isTradable", "tradeEnabled")
    tradable = bool(tradable_value if tradable_value is not None else True)
    broker_state = _value(row, "marketState", "sessionState", "status")
    state = str(broker_state or ("closed" if tradable_value is not None and not tradable else "unknown")).lower()
    clean = re.sub(r"[^A-Z]", "", symbol.upper())
    return {
        "instrument_id": str(instrument_id), "broker_symbol": symbol, "display_symbol": str(_value(row, "displaySymbol") or symbol),
        "description": _value(row, "description", "localizedName"), "asset_class": asset_class, "market_group": group,
        "classification_source": source, "base_currency": _value(row, "baseCurrency") or (clean[:3] if group.startswith("forex_") and len(clean) == 6 else None),
        "quote_currency": _value(row, "quoteCurrency") or (clean[3:] if group.startswith("forex_") and len(clean) == 6 else None),
        "tick_size": _value(row, "tickSize", "priceIncrement"), "tick_value": _value(row, "tickValue"),
        "contract_size": _value(row, "contractSize"), "minimum_quantity": _value(row, "minQty", "minQuantity"),
        "maximum_quantity": _value(row, "maxQty", "maxQuantity"), "quantity_increment": _value(row, "qtyStep", "quantityIncrement"),
        "margin": _value(row, "marginRate", "marginRequirement"), "currently_tradable": tradable,
        "market_state": state, "_market_state_from_broker": broker_state is not None, "_routes": routes,
    }


def resolve_universe(instruments: list[dict[str, Any]], universe: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = {str(item) for item in universe.get("excluded_instrument_ids", [])}
    mode = universe.get("mode", "all_available")
    if mode == "groups": selected = {item for item in universe.get("groups", [])}; result = [i for i in instruments if i["market_group"] in selected]
    elif mode == "custom": selected = {str(item) for item in universe.get("included_instrument_ids", [])}; result = [i for i in instruments if i["instrument_id"] in selected]
    else: result = list(instruments)
    return [item for item in result if item["instrument_id"] not in excluded]


def market_is_open(instrument: dict[str, Any], now: datetime) -> bool:
    """Prefer broker state; use conservative group hours only when metadata is absent."""
    state = str(instrument.get("market_state") or "").lower()
    if state in {"closed", "halted", "suspended", "disabled"}: return False
    if state in {"open", "trading", "tradable", "active"} and instrument.get("_market_state_from_broker"):
        return bool(instrument.get("currently_tradable", True))
    if not instrument.get("currently_tradable", True): return False
    current = now.astimezone(timezone.utc); group = instrument.get("market_group")
    if group == "crypto": return True
    if group.startswith("forex_"):
        # Conservative retail-FX fallback: Sunday 22:00 through Friday 22:00 UTC.
        return not (current.weekday() == 5 or (current.weekday() == 6 and current.hour < 22) or (current.weekday() == 4 and current.hour >= 22))
    if group in {"metals", "energies", "indices", "equities"}: return current.weekday() < 5
    return state in {"open", "trading", "tradable", "active"}


def screen_candidates(instruments: list[dict[str, Any]], *, maximum_screened: int,
                      maximum_deeply_analyzed: int) -> dict[str, Any]:
    """Bounded, metadata-only first stage; callers fetch full histories only for `deep_candidates`."""
    bounded = instruments[:max(0, maximum_screened)]
    viable = [item for item in bounded if item.get("currently_tradable") and str(item.get("market_state", "open")).lower() not in {"closed","halted","suspended"}]
    ranked = sorted(viable, key=lambda item: (item.get("tick_size") is None, item.get("market_group") == "other", item.get("broker_symbol", "")))
    deep = ranked[:max(0, maximum_deeply_analyzed)]
    return {"candidates_screened":len(bounded),"candidate_ids":[item["instrument_id"] for item in bounded],
        "candidates_deeply_analyzed":len(deep),"deep_candidates":[item["instrument_id"] for item in deep]}


def classify_orders(positions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> dict[str, Any]:
    position_ids = {str(_value(row, "positionId", "id")) for row in positions if _value(row, "positionId", "id") is not None}
    stop_ids = {str(_value(row, "stopLossId")) for row in positions if _value(row, "stopLossId") is not None}
    tp_ids = {str(_value(row, "takeProfitId")) for row in positions if _value(row, "takeProfitId") is not None}
    counts = {key: 0 for key in ("pending_entry", "protective_stop_loss", "protective_take_profit", "position_close", "cancelled", "filled", "rejected", "unknown")}
    classified = []
    for row in orders:
        order_id = str(_value(row, "orderId", "id") or "")
        position_id = str(_value(row, "positionId") or "")
        status = str(_value(row, "status", "state") or "").lower()
        order_type = str(_value(row, "type", "orderType") or "").lower()
        reduce_only = bool(_value(row, "reduceOnly", "isReduceOnly"))
        if order_id in stop_ids or (position_id in position_ids and "stop" in order_type): category = "protective_stop_loss"
        elif order_id in tp_ids or (position_id in position_ids and ("take" in order_type or "limit" in order_type)): category = "protective_take_profit"
        elif reduce_only or (position_id in position_ids and any(word in order_type for word in ("close", "market"))): category = "position_close"
        elif status in {"cancelled", "canceled"}: category = "cancelled"
        elif status in {"filled", "executed"}: category = "filled"
        elif status in {"rejected", "refused"}: category = "rejected"
        elif status in {"open", "working", "pending", "accepted", "new"} and not position_id: category = "pending_entry"
        else: category = "unknown"
        counts[category] += 1; classified.append({"order_id": order_id or None, "position_id": position_id or None, "category": category})
    return {"counts": counts, "orders": classified}


def count_open_positions(positions: list[dict[str, Any]]) -> int:
    ids: set[str] = set()
    for row in positions:
        position_id = _value(row, "positionId", "id")
        status = str(_value(row, "status", "state") or "open").lower()
        quantity = _value(row, "qty", "quantity", "size")
        if position_id is None or status in {"closed", "cancelled", "canceled", "filled"}: continue
        if quantity is not None:
            try:
                if float(quantity) == 0: continue
            except (TypeError, ValueError):
                continue
        ids.add(str(position_id))
    return len(ids)


@dataclass(frozen=True)
class SizingResult:
    approved: bool; final_risk_pct: float; risk_amount: float; stop_distance: float
    quantity_before_rounding: float; quantity: float; estimated_margin: float | None; rejection_reasons: list[str]
    audit: dict[str, Any]


def authorized_capital(*, account_equity: float, allocation: dict[str, Any],
                       realized_profile_pnl: float = 0.0) -> float:
    if account_equity < 0: raise ValueError("account_equity must be non-negative")
    mode=allocation.get("mode","full_account")
    if mode=="fixed_amount": base=float(allocation.get("fixed_amount") or 0)
    elif mode=="equity_percentage": base=account_equity*float(allocation.get("equity_percentage") or 0)/100
    else: base=account_equity
    if allocation.get("compounding_mode")=="realized_pnl":base+=realized_profile_pnl
    # Disabled fixed allocations never compound; periodic rebalance changes only when
    # equity/percentage or the explicitly saved fixed allocation changes.
    return min(account_equity,max(0.0,base))


def build_capital_state(*, account_equity: float, allocation: dict[str, Any], risk_policy: dict[str, Any],
                        realized_profile_pnl: float = 0.0, unrealized_profile_pnl: float = 0.0,
                        open_risk_amount: float = 0.0, margin_used: float = 0.0,
                        margin_reserved: float = 0.0) -> dict[str, float]:
    capital=authorized_capital(account_equity=account_equity,allocation=allocation,
        realized_profile_pnl=realized_profile_pnl)
    risk_base=capital if allocation.get("risk_base")=="allocated_capital" else account_equity
    margin_budget=capital*float(allocation.get("maximum_margin_utilization_pct",70))/100
    risk_budget=capital*float(risk_policy.get("maximum_total_open_risk_pct",1))/100
    return {"account_equity":account_equity,"authorized_capital":capital,"risk_base_amount":risk_base,
        "margin_used":margin_used,"margin_reserved":margin_reserved,
        "remaining_margin_budget":max(0.0,margin_budget-margin_used-margin_reserved),
        "open_risk_amount":open_risk_amount,"remaining_risk_budget":max(0.0,risk_budget-open_risk_amount),
        "realized_profile_pnl":realized_profile_pnl,"unrealized_profile_pnl":unrealized_profile_pnl}


def profile_attributed_capital_metrics(positions:list[dict[str,Any]],orders:list[dict[str,Any]],
                                       *,owned_position_ids:set[str],owned_order_ids:set[str],
                                       order_history:list[dict[str,Any]]|None=None)->dict[str,float]:
    classified=classify_orders(positions,orders);categories={item["order_id"]:item["category"] for item in classified["orders"]}
    realized=unrealized=open_risk=margin_used=margin_reserved=0.0
    for row in positions:
        position_id=str(_value(row,"positionId","id") or "")
        if position_id not in owned_position_ids:continue
        unrealized+=float(_value(row,"unrealizedPnl","openPnl","pnl") or 0)
        margin_used+=float(_value(row,"marginUsed","usedMargin","margin") or 0)
        explicit_risk=_value(row,"openRisk","riskAmount","stopLossRisk")
        if explicit_risk is not None:open_risk+=max(0.0,float(explicit_risk))
    for row in orders:
        order_id=str(_value(row,"orderId","id") or "")
        if order_id not in owned_order_ids or categories.get(order_id)!="pending_entry":continue
        margin_reserved+=float(_value(row,"margin","reservedMargin","marginRequirement") or 0)
    for row in order_history or []:
        order_id=str(_value(row,"orderId","id") or "")
        if order_id in owned_order_ids:
            realized+=float(_value(row,"realizedPnl","closedPnl","pnl") or 0)
    return {"realized_profile_pnl":realized,"unrealized_profile_pnl":unrealized,
        "open_risk_amount":open_risk,"margin_used":margin_used,"margin_reserved":margin_reserved}


def deterministic_size(*, equity: float, entry: float, stop: float, loss_per_price_unit: float,
                       minimum_quantity: float, maximum_quantity: float, quantity_increment: float,
                       risk_policy: dict[str, Any], proposed_multiplier: float = 1.0,
                       current_open_risk_pct: float = 0.0, estimated_margin_per_unit: float | None = None,
                       available_margin: float | None = None) -> SizingResult:
    reasons: list[str] = []; distance = abs(entry - stop)
    mode = risk_policy.get("mode", "fixed"); base = float(risk_policy.get("base_risk_pct", 0.25))
    proposed = float(risk_policy.get("fixed_risk_pct", 0.25)) if mode == "fixed" else base * max(0.0, proposed_multiplier)
    final = proposed if mode == "fixed" else min(float(risk_policy.get("maximum_risk_pct", .5)), max(float(risk_policy.get("minimum_risk_pct", .1)), proposed))
    cap = float(risk_policy.get("maximum_total_open_risk_pct", 100)) - current_open_risk_pct
    final = max(0.0, min(final, cap))
    if equity <= 0 or distance <= 0 or loss_per_price_unit <= 0: reasons.append("invalid_sizing_inputs")
    amount = equity * final / 100; per_unit = distance * loss_per_price_unit
    raw = amount / per_unit if per_unit > 0 else 0
    step = quantity_increment if quantity_increment > 0 else 1
    rounded = math.floor(raw / step + 1e-12) * step
    rounded = min(rounded, maximum_quantity)
    if rounded < minimum_quantity: reasons.append("quantity_below_broker_minimum"); rounded = 0
    margin = rounded * estimated_margin_per_unit if estimated_margin_per_unit is not None else None
    if margin is not None and available_margin is not None and margin > available_margin: reasons.append("insufficient_margin")
    margin_cap = risk_policy.get("maximum_margin_utilization_pct")
    if margin_cap is not None and margin is not None and equity > 0 and margin / equity * 100 > float(margin_cap): reasons.append("maximum_margin_utilization_reached")
    audit = {"account_equity_used": equity, "base_risk_pct": base, "model_proposed_multiplier": proposed_multiplier,
             "deterministic_adjustment_factors": {"portfolio_risk_cap_pct": cap, "broker_increment": step},
             "final_risk_pct": final, "risk_amount": amount, "stop_distance": distance,
             "quantity_before_rounding": raw, "quantity_after_broker_rounding": rounded, "estimated_margin": margin,
             "rejection_reasons": reasons}
    return SizingResult(not reasons, final, amount, distance, raw, rounded, margin, reasons, audit)


def validate_exit_prices(*, side: str, entry: float, current_price: float, stop: float | None, take_profit: float | None,
                         tick_size: float, minimum_distance: float = 0, minimum_reward_to_risk: float = 0) -> dict[str, Any]:
    reasons: list[str] = []
    if tick_size <= 0: reasons.append("invalid_tick_size")
    normalize = lambda value: round(round(value / tick_size) * tick_size, 10) if value is not None and tick_size > 0 else value
    stop, take_profit = normalize(stop), normalize(take_profit)
    if stop is not None and ((side == "long" and stop >= min(entry, current_price)) or (side == "short" and stop <= max(entry, current_price))): reasons.append("invalid_stop_side")
    if take_profit is not None and ((side == "long" and take_profit <= max(entry, current_price)) or (side == "short" and take_profit >= min(entry, current_price))): reasons.append("invalid_take_profit_side")
    if stop is not None and abs(entry - stop) < minimum_distance: reasons.append("stop_below_broker_minimum_distance")
    reward_risk = abs(take_profit - entry) / abs(entry - stop) if stop is not None and take_profit is not None and stop != entry else None
    if reward_risk is not None and reward_risk < minimum_reward_to_risk: reasons.append("reward_to_risk_below_minimum")
    return {"approved": not reasons, "stop_loss": stop, "take_profit": take_profit, "reward_to_risk": reward_risk, "reasons": reasons}
