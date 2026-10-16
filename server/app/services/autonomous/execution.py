from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.brokers.tradelocker.mapping import TradeLockerMappingError, map_configured_rows
from app.config.settings import settings
from app.models.autonomous import AutonomousOrderProposal, ExecutionMode
from app.services.market_data.history import normalize_timeframe
from app.services.providers.errors import ProviderError
from app.services.providers.finnhub import FinnhubClient
from app.services.providers.fred import FredClient
from app.services.tradelocker.account_status import AccountStatusUnavailable, TradeLockerAccountStatusService
from app.services.tradelocker.accounts import AccountResolutionError, BrokerAccountResolver
from app.services.trading_policy import (classify_orders, count_open_positions, market_is_open,
    normalize_instrument, resolve_universe, screen_candidates, authorized_capital,
    build_capital_state, profile_attributed_capital_metrics)
from app.storage.brokers import BrokerConnection, BrokerRepository, BrokerStorageError
from app.storage.execution import ExecutionRepository, utcnow


logger = logging.getLogger(__name__)
ALLOWED_PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD")


class AutonomousExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: str = "blocked",
                 reasons: list[str] | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.status, self.reasons = code, status, reasons or [code]
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": "1.0", "status": self.status, "error": self.code,
            "message": str(self), "blocking_reasons": self.reasons, **self.details}


@dataclass(frozen=True)
class VerifiedDemoContext:
    user_sub: str
    connection_id: str
    account_id: str
    acc_num: str
    account_name: str | None
    currency: str | None
    environment: str
    server: str
    base_url: str
    execution_mode: ExecutionMode
    risk: dict[str, Any]
    connection: BrokerConnection
    profile_ref: str
    account_record_id: str
    account_alias: str
    connection_ref: str
    connection_label: str | None
    demo_classification: str


@dataclass(frozen=True)
class InstrumentMetadata:
    instrument_id: str
    route_id: str
    contract_size: float
    pip_size: float
    lot_step: float
    min_lots: float
    max_lots: float | None
    quote_currency: str
    minimum_stop_distance: float
    commission_per_lot: float
    leverage: float | None = None
    tick_size: float | None = None
    price_precision: int | None = None


def normalize_pair(pair: str) -> str:
    return pair.replace("/", "").replace("_", "").upper().strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalized_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    return {"long": "buy", "short": "sell"}.get(side, side)


def _normalized_order_type(value: Any) -> str:
    order_type = str(value or "").strip().lower().replace("_", "").replace("-", "")
    return {"stoplimit": "stop", "stopmarket": "stop"}.get(order_type, order_type)


def _string_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _normalized_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _find_value(value: Any, aliases: tuple[str, ...]) -> Any:
    targets = {alias.lower() for alias in aliases}
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in targets and item is not None and not isinstance(item, (dict, list)):
                return item
        for item in value.values():
            found = _find_value(item, aliases)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_value(item, aliases)
            if found is not None:
                return found
    return None


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise AutonomousExecutionError("position_size_unverifiable", f"Broker {field} is unverifiable.", status="rejected")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AutonomousExecutionError("position_size_unverifiable", f"Broker {field} is unverifiable.", status="rejected") from None
    if not math.isfinite(number) or number <= 0:
        raise AutonomousExecutionError("position_size_unverifiable", f"Broker {field} is unverifiable.", status="rejected")
    return number


def _nonnegative(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AutonomousExecutionError("broker_protection_unverifiable", f"Broker {field} is unverifiable.", status="rejected") from None
    if not math.isfinite(number) or number < 0:
        raise AutonomousExecutionError("broker_protection_unverifiable", f"Broker {field} is unverifiable.", status="rejected")
    return number


def parse_instrument_metadata(payload: dict[str, Any], pair: str) -> InstrumentMetadata:
    details, listing = payload.get("details", {}), payload.get("listing", {})
    combined = {"details": details, "listing": listing}
    routes = listing.get("routes", []) if isinstance(listing, dict) else []
    trade_route = next((r.get("id", r.get("routeId")) for r in routes if isinstance(r, dict) and str(r.get("type", "")).upper() == "TRADE"), None)
    route_id = trade_route or _find_value(details, ("tradeRouteId", "routeId"))
    instrument_id = payload.get("instrument_id")
    quote = _find_value(combined, ("quoteCurrency", "quotingCurrency", "currency")) or pair[3:]
    max_value = _find_value(combined, ("maxOrderQty", "maxQuantity", "maxLots", "maxLot"))
    precision_value = _find_value(combined, ("pricePrecision", "digits", "decimalPlaces"))
    precision = int(precision_value) if isinstance(precision_value, (int, float)) and 0 <= int(precision_value) <= 10 else None
    tick_value = _find_value(combined, ("tickSize", "minPriceIncrement", "priceIncrement"))
    explicit_pip = _find_value(combined, ("pipSize", "pipValue"))
    tick_size = (_positive(tick_value, "tick size") if tick_value is not None
        else 10 ** -precision if precision is not None else None)
    if explicit_pip is not None:
        pip_size = _positive(explicit_pip, "pip size")
    elif tick_size is not None and len(pair) == 6:
        # TradeLocker exposes fractional-pip tick tiers for these non-JPY forex pairs.
        pip_size = tick_size * 10 if pair[3:] != "JPY" else tick_size
    else:
        raise AutonomousExecutionError("position_size_unverifiable", "Broker pip size is unverifiable.", status="rejected")
    if tick_size is None:
        tick_size = pip_size
    if precision is None:
        precision = max(0, -Decimal(str(tick_size)).normalize().as_tuple().exponent)
    return InstrumentMetadata(
        instrument_id=str(instrument_id) if instrument_id is not None else "",
        route_id=str(route_id) if route_id is not None else "",
        contract_size=_positive(_find_value(combined, ("contractSize", "lotSize", "unitsPerLot")), "contract size"),
        pip_size=pip_size,
        lot_step=_positive(_find_value(combined, ("lotStep", "qtyStep", "quantityStep", "minOrderQtyIncrement")), "quantity increment"),
        min_lots=_positive(_find_value(combined, ("minOrderQty", "minQuantity", "minLots", "minLot")), "minimum quantity"),
        max_lots=_positive(max_value, "maximum quantity") if max_value is not None else None,
        quote_currency=str(quote).upper(),
        minimum_stop_distance=_nonnegative(
            _find_value(combined, ("minStopLossDistance", "stopLossDistance", "stopsLevel")) or tick_size,
            "minimum stop distance",
        ),
        commission_per_lot=_nonnegative(
            _find_value(combined, ("commissionPerLot", "roundTurnCommission")) or 0,
            "commission",
        ),
        leverage=_positive(_find_value(combined,("leverage","effectiveLeverage")),"leverage") if _find_value(combined,("leverage","effectiveLeverage")) is not None else None,
        tick_size=tick_size, price_precision=precision,
    )


def calculate_reward_risk(
    *, entry: float, stop_loss: float, take_profit: float, bid: float, ask: float,
    metadata: InstrumentMetadata, minimum_reward_risk: float, side: str,
    order_type: str,
) -> dict[str, Any]:
    """Return conservative broker-rounded reward/risk with one spread cost."""
    tick = Decimal(str(metadata.tick_size or metadata.pip_size))

    def rounded(value: float) -> Decimal:
        return (Decimal(str(value)) / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick

    rounded_entry, rounded_stop, rounded_target = map(rounded, (entry, stop_loss, take_profit))
    rounded_bid, rounded_ask = rounded(bid), rounded(ask)
    gross_risk = abs(rounded_entry - rounded_stop)
    gross_reward = abs(rounded_target - rounded_entry)
    spread = max(Decimal("0"), rounded_ask - rounded_bid)
    adjusted_risk = gross_risk + spread
    adjusted_reward = max(Decimal("0"), gross_reward - spread)
    reward_risk = adjusted_reward / adjusted_risk if adjusted_risk else Decimal("0")
    return {
        "rounded_entry": float(rounded_entry), "rounded_stop_loss": float(rounded_stop),
        "rounded_take_profit": float(rounded_target), "quote_bid": float(rounded_bid),
        "quote_ask": float(rounded_ask), "tick_size": float(tick),
        "price_precision": metadata.price_precision,
        "entry_price_basis": "requested_limit" if order_type == "limit" else "ask" if side == "long" else "bid",
        "gross_risk_distance": float(gross_risk), "gross_reward_distance": float(gross_reward),
        "spread_adjustment": float(spread), "risk_distance": float(adjusted_risk),
        "reward_distance": float(adjusted_reward), "reward_risk": float(reward_risk),
        "minimum_reward_risk": float(Decimal(str(minimum_reward_risk))),
        "comparison_tolerance": 0.0,
    }


def calculate_broker_position_size(
    *, balance: float, available_funds: float, risk_percent: float,
    entry: float, stop_loss: float, metadata: InstrumentMetadata,
    quote_to_account_rate: float, spread: float = 0.0,
    commission_per_lot: float = 0.0, risk_base_amount: float | None = None,
    remaining_allocation_margin: float | None = None,
) -> dict[str, float]:
    if balance <= 0 or available_funds <= 0:
        raise AutonomousExecutionError("insufficient_account_funds", "Account balance and available funds must be positive.", status="rejected")
    conversion = _positive(quote_to_account_rate, "currency conversion rate")
    price_risk = float(
        abs(Decimal(str(entry)) - Decimal(str(stop_loss)))
        + max(Decimal("0"), Decimal(str(spread)))
    )
    risk_per_lot = price_risk * metadata.contract_size * conversion + max(0.0, commission_per_lot)
    if risk_per_lot <= 0:
        raise AutonomousExecutionError("position_size_unverifiable", "The broker-specific position size could not be verified safely.", status="rejected")
    risk_base = balance if risk_base_amount is None else risk_base_amount
    if risk_base <= 0:
        raise AutonomousExecutionError("capital_allocation_exhausted", "The profile has no authorized risk capital remaining.", status="rejected")
    risk_budget = risk_base * risk_percent / 100.0
    raw_lots = risk_budget / risk_per_lot
    step = Decimal(str(metadata.lot_step))
    lots = float((Decimal(str(raw_lots)) / step).to_integral_value(rounding=ROUND_DOWN) * step)
    if lots < metadata.min_lots or (metadata.max_lots is not None and lots > metadata.max_lots):
        raise AutonomousExecutionError("position_size_unverifiable", "The calculated size violates broker quantity limits.", status="rejected")
    estimated = lots * risk_per_lot
    if metadata.leverage is None:
        raise AutonomousExecutionError("margin_unverifiable", "Broker leverage is unavailable, so margin cannot be verified.", status="rejected")
    estimated_margin = lots * metadata.contract_size * entry * conversion / metadata.leverage
    if estimated_margin > available_funds:
        raise AutonomousExecutionError("insufficient_margin", "Estimated margin exceeds available funds.", status="rejected")
    if remaining_allocation_margin is not None and estimated_margin > remaining_allocation_margin:
        raise AutonomousExecutionError("capital_margin_limit", "Estimated margin exceeds the profile allocation margin budget.", status="rejected")
    return {
        "lot_size": lots, "quantity": lots * metadata.contract_size,
        "estimated_risk": estimated, "risk_percent": estimated / risk_base * 100,
        "risk_budget":risk_budget,"risk_base_amount":risk_base,
        "estimated_margin":estimated_margin,"available_funds_after_margin":available_funds-estimated_margin,
    }


class AutonomousDemoService:
    def __init__(
        self, *, broker_repository: BrokerRepository | None = None,
        execution_repository: ExecutionRepository | None = None,
        client_factory: Callable[..., TradeLockerClient] = TradeLockerClient,
        account_status_service: TradeLockerAccountStatusService | None = None,
        finnhub_factory: Callable[[], FinnhubClient] = FinnhubClient,
        fred_factory: Callable[[], FredClient] = FredClient,
    ) -> None:
        self.brokers = broker_repository or BrokerRepository()
        self.execution = execution_repository or ExecutionRepository()
        self.client_factory = client_factory
        self.account_status_service = account_status_service or TradeLockerAccountStatusService(repository=self.brokers, client_factory=client_factory)
        self.finnhub_factory, self.fred_factory = finnhub_factory, fred_factory

    def _kill_switch(self,user_sub:str)->bool:
        return self.execution.get_autonomous_controls(user_sub)["global_autonomous_kill_switch"]

    @staticmethod
    def _provider_requirements(context: VerifiedDemoContext, autonomous: bool) -> tuple[bool, bool]:
        config = context.risk.get("strategy_config", {})
        finnhub_required = autonomous or bool(
            config.get("require_finnhub_for_manual") or config.get("required_market_events")
        )
        fred_required = bool(config.get("required_macro_series"))
        return finnhub_required, fred_required

    @staticmethod
    def _snapshot_failure(
        component: str, diagnostics: dict[str, Any] | None = None
    ) -> AutonomousExecutionError:
        safe_component = component.replace("-", "_")
        return AutonomousExecutionError(
            "market_snapshot_unavailable",
            f"The required TradeLocker {safe_component.replace('_', ' ')} component is unavailable or unmappable.",
            reasons=[f"{safe_component}_unavailable"],
            details={"missing_component": safe_component, **(diagnostics or {})},
        )

    async def context(self, user_sub: str, profile_ref: str, *, require_mode: bool = True,
                      allow_autonomous: bool = False) -> VerifiedDemoContext:
        """Resolve execution exclusively through an owned profile; dashboard defaults are never read here."""
        try:
            resolved = BrokerAccountResolver(self.brokers).resolve(user_sub, profile=profile_ref)
        except AccountResolutionError as exc:
            raise AutonomousExecutionError(exc.code, str(exc)) from exc
        profile = next((item for item in self.brokers.list_profiles(user_sub) if item["public_id"] == resolved.profile_ref), None)
        if profile is None:
            raise AutonomousExecutionError("profile_not_found", "The execution profile is unavailable.")
        mode = ExecutionMode.DEMO_AUTONOMOUS if allow_autonomous else ExecutionMode.DEMO_MANUAL
        risk = self.execution.get_or_create_settings(user_sub, resolved.connection_id, resolved.account_id, resolved.account_number)
        risk.update(profile.get("risk") or {})
        v2_risk=(profile.get("profile_v2") or {}).get("risk_policy") or {}
        if v2_risk:
            risk["maximum_total_open_risk_pct"]=float(v2_risk.get("maximum_total_open_risk_pct",1))
        if not 0 < float(risk["risk_per_trade_percent"]) <= 1.0:
            raise AutonomousExecutionError("risk_policy_invalid", "Risk per trade must be greater than zero and no more than 1%.")
        risk["risk_per_trade_percent"] = min(float(risk["risk_per_trade_percent"]), 1.0)
        risk["daily_loss_limit_percent"] = min(float(risk.get("daily_loss_limit_percent", 3.0)), 3.0)
        risk["drawdown_cutoff_percent"] = min(float(risk.get("drawdown_cutoff_percent", 10.0)), 10.0)
        risk["maximum_open_positions"] = min(max(int(risk.get("maximum_open_positions", 1)), 1), 100)
        risk["maximum_pending_orders"] = min(max(int(risk.get("maximum_pending_orders", 1)), 0), 100)
        risk["maximum_new_entries_per_day"] = min(max(int(risk.get("maximum_new_entries_per_day", 2)), 1), 100)
        risk["minimum_reward_risk"] = max(float(risk.get("minimum_reward_risk", 1.5)), 1.5)
        if profile.get("allowed_instruments"):
            risk["allowed_pairs"] = [normalize_pair(pair) for pair in profile["allowed_instruments"]]
        risk["strategy_name"] = profile.get("strategy_name",risk.get("strategy_name","ai_forex_confluence"))
        risk["strategy_version"] = profile.get("strategy_version",risk.get("strategy_version","1"))
        risk["strategy_config"] = profile.get("strategy_config") or {}
        risk["execution_mode"] = mode.value
        risk["capital_allocation"]=(profile.get("profile_v2") or {}).get("capital_allocation") or {
            "mode":"full_account","risk_base":"account_equity","compounding_mode":"disabled",
            "maximum_margin_utilization_pct":70.0,"allow_shared_capital":False}
        connection = BrokerConnection(connection_id=resolved.connection_id, connection_ref=resolved.connection_ref,
            base_url=resolved.base_url, username=resolved.username, password=resolved.password,
            server=resolved.server, account_id=resolved.account_id, account_number=resolved.account_number,
            environment=resolved.environment, label=resolved.connection_label)
        base_matches = _normalized_url(connection.base_url) == _normalized_url(settings.tradelocker_demo_base_url)
        if connection.environment != "demo" or resolved.demo_classification != "demo" or not base_matches:
            raise AutonomousExecutionError("demo_environment_verification_failed", "Order execution is available only for a verified TradeLocker demo account.", reasons=["account_not_demo"])
        client = self._client(connection)
        try:
            discovered = await client.get_accounts()
            profile_v2=profile.get("profile_v2") or {}
            if (profile_v2.get("trading_policy") or {}).get("mode")=="adaptive" and hasattr(client,"get_symbols"):
                symbols_payload=await client.get_symbols()
                catalog=[item for row in client._instrument_rows(symbols_payload) if (item:=normalize_instrument(row)) is not None]
                selected=resolve_universe(catalog,profile_v2.get("market_universe") or {"mode":"all_available"})
                selected=[item for item in selected if market_is_open(item,utcnow())]
                screening=screen_candidates(selected,maximum_screened=settings.autonomous_maximum_instruments_screened,
                    maximum_deeply_analyzed=settings.autonomous_maximum_instruments_deeply_analyzed)
                deep=set(screening["deep_candidates"])
                risk["allowed_pairs"]=[item["broker_symbol"] for item in selected if item["instrument_id"] in deep]
                risk["screening"]={**screening,"selected_instruments":len(selected),
                    "maximum_upstream_requests_per_run":settings.autonomous_maximum_upstream_requests_per_run,
                    "maximum_run_duration_seconds":settings.autonomous_maximum_run_duration_seconds}
        except TradeLockerError:
            raise AutonomousExecutionError("broker_unreachable", "TradeLocker account discovery is unavailable.") from None
        finally:
            await client.aclose()
        account = next((row for row in discovered.get("accounts", []) if isinstance(row, dict) and str(row.get("accountId")) == connection.account_id and str(row.get("accNum")) == connection.account_number), None) if isinstance(discovered, dict) else None
        if account is None:
            raise AutonomousExecutionError("demo_environment_verification_failed", "The profile-bound demo account could not be verified during account discovery.")
        return VerifiedDemoContext(
            user_sub=user_sub, connection_id=connection.connection_id,
            account_id=connection.account_id, acc_num=connection.account_number,
            account_name=account.get("name"), currency=account.get("currency"),
            environment=connection.environment, server=connection.server,
            base_url=connection.base_url, execution_mode=mode, risk=risk,
            connection=connection, profile_ref=resolved.profile_ref or profile_ref,
            account_record_id=resolved.account_record_id, account_alias=resolved.account_alias,
            connection_ref=resolved.connection_ref, connection_label=resolved.connection_label,
            demo_classification=resolved.demo_classification,
        )

    def _client(
        self, connection: BrokerConnection, context: VerifiedDemoContext | None = None
    ) -> TradeLockerClient:
        cache_scope = ({
            "cache_user_id": context.user_sub,
            "cache_connection_id": context.connection_ref,
            "cache_account_record_id": context.account_record_id,
        } if context is not None else {})
        return self.client_factory(
            base_url=connection.base_url, username=connection.username,
            password=connection.password, server=connection.server,
            account_id=connection.account_id, account_number=connection.account_number,
            **cache_scope,
        )

    async def _broker_risk_counts(self,context:VerifiedDemoContext)->dict[str,Any]|None:
        client=self._client(context.connection,context)
        if not all(hasattr(client,name) for name in ("get_config","get_open_positions","get_orders")):
            await client.aclose();return None
        try:
            config=await client.get_config()
            positions=map_configured_rows(config_response=config,data_response=await client.get_open_positions(),
                config_key="positionsConfig",data_key="positions")
            orders=map_configured_rows(config_response=config,data_response=await client.get_orders(),
                config_key="ordersConfig",data_key="orders")
        finally:await client.aclose()
        classified=classify_orders(positions,orders)["counts"]
        ownership=self.execution.profile_broker_ids(context.user_sub,context.profile_ref)
        metrics=profile_attributed_capital_metrics(positions,orders,owned_position_ids=ownership["position_ids"],owned_order_ids=ownership["order_ids"])
        metrics["open_risk_amount"]=max(metrics["open_risk_amount"],ownership["estimated_open_risk"])
        metrics["margin_used"]=max(metrics["margin_used"],ownership["estimated_margin_used"])
        return {"open_positions":count_open_positions(positions),"pending_entry_orders":classified["pending_entry"],
            "protective_stop_orders":classified["protective_stop_loss"],
            "protective_take_profit_orders":classified["protective_take_profit"],"capital_metrics":metrics}

    async def status(self, user_sub: str, profile_ref: str) -> dict[str, Any]:
        reasons = []
        context = None
        try:
            context = await self.context(user_sub, profile_ref, require_mode=False, allow_autonomous=True)
        except AutonomousExecutionError as exc:
            reasons.extend(exc.reasons)
        controls=self.execution.get_autonomous_controls(user_sub)
        autonomous_reasons=list(reasons)
        if controls["global_autonomous_kill_switch"]:autonomous_reasons.append("global_autonomous_kill_switch_enabled")
        if not controls["demo_autonomous_enabled"]:autonomous_reasons.append("demo_autonomous_disabled")
        finnhub_required, fred_required = self._provider_requirements(context, False) if context else (False, False)
        if finnhub_required and (not settings.finnhub_enabled or not settings.finnhub_api_key):
            reasons.append("provider_unavailable")
        if fred_required and (not settings.fred_enabled or not settings.fred_api_key):
            reasons.append("required_macro_provider_unavailable")
        account = None
        broker_counts = None
        if context:
            try:
                account = await self.account_status_service.retrieve(user_sub, context.account_alias)
                broker_counts = await self._broker_risk_counts(context)
                if account.balance <= 0 or account.projected_balance <= 0 or account.available_funds <= 0:
                    reasons.append("account_funds_unavailable")
                open_count=broker_counts["open_positions"] if broker_counts else account.positions_count
                pending_count=broker_counts["pending_entry_orders"] if broker_counts else account.pending_orders_count
                if open_count >= context.risk["maximum_open_positions"]:
                    reasons.append("maximum_open_positions_reached")
                if pending_count >= context.risk["maximum_pending_orders"]:
                    reasons.append("maximum_pending_orders_reached")
            except (AccountStatusUnavailable,TradeLockerError,TradeLockerMappingError):
                reasons.append("account_status_unavailable")
        autonomous_reasons=list(dict.fromkeys([*autonomous_reasons,*reasons]))
        capital_state=(build_capital_state(account_equity=account.projected_balance,
            allocation=context.risk["capital_allocation"],risk_policy={"maximum_total_open_risk_pct":context.risk.get("maximum_total_open_risk_pct",1)},
            **broker_counts["capital_metrics"]) if account and broker_counts and context else None)
        return {
            "schema_version": "1.0", "status": "ready" if not reasons else "blocked",
            "account_environment": context.environment if context else None,
            "execution_mode": "manual", "execution_mode_deprecated":True,
            "kill_switch": controls["global_autonomous_kill_switch"], "global_kill_switch":controls["global_autonomous_kill_switch"],
            "demo_autonomous_enabled":controls["demo_autonomous_enabled"],"profile_enabled":context is not None,
            "account_available":account is not None,"autonomous_active":not autonomous_reasons,
            "legacy_arming_deprecated":True,"armed":None,"armed_until":None,"shadow_mode":None,"strategy_enabled": True,
            "can_submit_demo_orders": not reasons, "blocking_reasons": list(dict.fromkeys(reasons)),
            "autonomous_blocking_reasons":list(dict.fromkeys(autonomous_reasons)),
            "profile_ref":context.profile_ref if context else profile_ref,
            "account_alias":context.account_alias if context else None,
            "broker":context.connection_label if context else None,
            "confirmed_demo":bool(context and context.demo_classification == "demo"),
            "balance":account.balance if account else None,"equity":account.projected_balance if account else None,
            "available_funds":account.available_funds if account else None,
            "positions_count":broker_counts["open_positions"] if broker_counts else account.positions_count if account else None,
            "pending_orders_count":broker_counts["pending_entry_orders"] if broker_counts else account.pending_orders_count if account else None,
            "protective_stop_orders":broker_counts["protective_stop_orders"] if broker_counts else None,
            "protective_take_profit_orders":broker_counts["protective_take_profit_orders"] if broker_counts else None,
            "capital_state":capital_state,
            "limits":context.risk if context else None,"allowed_pairs":context.risk["allowed_pairs"] if context else [],
            "ready_for_preview":not reasons,"ready_for_submission":not reasons,
        }

    async def _provider_state(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        providers: dict[str, Any] = {}
        blackouts: list[dict[str, Any]] = []
        context: dict[str, Any] = {"finnhub": {}, "fred": {}}
        finn = self.finnhub_factory()
        try:
            events = await finn.economic_calendar(date.today(), date.today(), 100)
            news = await finn.market_news("forex", 25)
            providers["finnhub"] = {"enabled": settings.finnhub_enabled, "required": True, "available": True, "stale": False}
            context["finnhub"] = {
                "economic_events": [
                    {"scheduled_at": event.scheduled_at.isoformat() if event.scheduled_at else None,
                     "currency": event.currency, "country": event.country, "impact": event.impact}
                    for event in events
                ],
                "news_item_count": len(news),
                "latest_news_at": max((item.published_at for item in news), default=None).isoformat() if news else None,
                "related_symbols": sorted({symbol for item in news for symbol in item.related_symbols})[:50],
            }
            now = utcnow()
            for event in events:
                if event.scheduled_at and abs((event.scheduled_at - now).total_seconds()) <= settings.autonomous_news_blackout_minutes * 60:
                    blackouts.append({"scheduled_at": event.scheduled_at.isoformat(), "currency": event.currency, "impact": event.impact})
        except ProviderError:
            providers["finnhub"] = {"enabled": settings.finnhub_enabled, "required": True, "available": False, "stale": True}
        finally:
            await finn.aclose()
        fred = self.fred_factory()
        try:
            releases = await fred.release_dates(date.today(), date.today(), 25)
            providers["fred"] = {"enabled": settings.fred_enabled, "required": False, "available": True, "stale": False}
            context["fred"] = {
                "release_dates": [
                    {"release_id": item.release_id, "date": item.date.isoformat()}
                    for item in releases
                ]
            }
        except ProviderError:
            providers["fred"] = {"enabled": settings.fred_enabled, "required": False, "available": False, "stale": True}
        finally:
            await fred.aclose()
        return providers, blackouts, context

    async def snapshot(self, user_sub: str, profile_ref: str, symbol: str | None = None, *, autonomous: bool=False) -> dict[str, Any]:
        context = await self.context(user_sub, profile_ref, allow_autonomous=autonomous)
        if autonomous:
            controls=self.execution.get_autonomous_controls(user_sub)
            if controls["global_autonomous_kill_switch"]:
                raise AutonomousExecutionError("global_autonomous_kill_switch_enabled", "The global autonomous kill switch blocks new autonomous snapshots.")
            if not controls["demo_autonomous_enabled"]:
                raise AutonomousExecutionError("demo_autonomous_disabled", "Demo autonomous trading is disabled.")
        try:
            account = await self.account_status_service.retrieve(user_sub, context.account_alias)
        except (AccountStatusUnavailable, TradeLockerError, TradeLockerMappingError):
            raise self._snapshot_failure("account_status") from None
        account_json = account.model_dump(mode="json")
        if account.account.account_alias != context.account_alias:
            raise AutonomousExecutionError("profile_account_context_mismatch", "The profile-bound account changed during snapshot retrieval.")
        providers, blackouts, provider_context = await self._provider_state()
        finnhub_required, fred_required = self._provider_requirements(context, autonomous)
        providers["finnhub"]["required"] = finnhub_required
        providers["fred"]["required"] = fred_required
        warnings = []
        if not providers["finnhub"]["available"] or providers["finnhub"]["stale"]:
            warnings.append("provider_unavailable")
        if not providers["fred"]["available"] or providers["fred"]["stale"]:
            warnings.append("required_macro_provider_unavailable" if fred_required else "macro_provider_unavailable")
        if blackouts:
            warnings.append("news_blackout")
        client = self._client(context.connection, context)
        market: dict[str, Any] = {}
        try:
            try:
                config = await client.get_config()
            except TradeLockerError:
                raise self._snapshot_failure("trade_config") from None
            try:
                raw_positions = await client.get_open_positions()
            except TradeLockerError:
                raise self._snapshot_failure("positions") from None
            try:
                positions = map_configured_rows(config_response=config, data_response=raw_positions,
                    config_key="positionsConfig", data_key="positions")
            except TradeLockerMappingError:
                raise self._snapshot_failure("positions_mapping") from None
            try:
                raw_orders = await client.get_orders()
            except TradeLockerError:
                raise self._snapshot_failure("pending_orders") from None
            try:
                orders = map_configured_rows(config_response=config, data_response=raw_orders,
                    config_key="ordersConfig", data_key="orders")
            except TradeLockerMappingError:
                raise self._snapshot_failure("pending_orders_mapping") from None
            order_history = []
            try:
                raw_history = await client.get_orders_history()
                order_history = map_configured_rows(config_response=config, data_response=raw_history,
                    config_key="ordersHistoryConfig", data_key="ordersHistory")
            except (TradeLockerError, TradeLockerMappingError):
                if autonomous:
                    raise self._snapshot_failure("order_history") from None
                warnings.append("order_history_unavailable")
            requested_pair=normalize_pair(symbol) if symbol else None
            if requested_pair and requested_pair not in context.risk["allowed_pairs"]:
                raise AutonomousExecutionError("pair_not_allowed","The symbol is not allowed by this execution profile.")
            for pair in [requested_pair] if requested_pair else context.risk["allowed_pairs"]:
                try:
                    quote = await client.get_quote(pair)
                    bid, ask = self._quote(quote)
                    if ask < bid:
                        raise ValueError
                except (TradeLockerError, AutonomousExecutionError, ValueError):
                    raise self._snapshot_failure(f"{pair.lower()}_quote") from None
                try:
                    instrument_payload = await client.get_instrument_details(pair)
                    instrument = parse_instrument_metadata(instrument_payload, pair)
                except (TradeLockerError, AutonomousExecutionError):
                    raise self._snapshot_failure(f"{pair.lower()}_instrument_metadata") from None
                d1 = h4 = h1 = m15 = None
                timeframe_results: dict[str, dict[str, Any]] = {}
                timeframe_candles: dict[str, list[dict[str, Any]]] = {}
                timeframe_failures: list[str] = []
                if autonomous:
                    for timeframe, count in (("1d", 190), ("4h", 250), ("1h", 200), ("15m", 200)):
                        try:
                            series = await client.get_candles(
                                pair, timeframe, count, minimum_usable=50
                            )
                        except TradeLockerError as exc:
                            diagnostics = {
                                "requested_timeframe": timeframe,
                                "provider_timeframe_sent": normalize_timeframe(timeframe),
                                "http_status": exc.status_code,
                                "broker_error_category": exc.code,
                                "rows_received": 0,
                                "mapping_failure": exc.details.get("mapping_failure"),
                                "cache_hit": False,
                                "cache_fresh": False,
                                "upstream_request_made": True,
                                "usable_count": 0,
                                "newest_completed_timestamp": None,
                                "is_sufficient": False,
                            }
                            diagnostics.update(exc.details)
                            reason = exc.code if exc.code in {
                                "unsupported_timeframe", "no_candles_returned",
                                "tradelocker_rate_limit_exhausted",
                            } else "provider_request_failed"
                            timeframe_results[timeframe] = {
                                "status": "blocked", "symbol": pair,
                                "requested_timeframe": timeframe,
                                "provider_timeframe": normalize_timeframe(timeframe),
                                "source": "direct",
                                "metadata": diagnostics,
                                "blocking_reasons": [reason], "warnings": [],
                            }
                            timeframe_failures.append(
                                f"{pair.lower()}_candles_{timeframe}_{reason}"
                            )
                            if reason == "tradelocker_rate_limit_exhausted":
                                timeframe_failures.append(reason)
                            continue
                        canonical = series.canonical_dict() if hasattr(series, "canonical_dict") else {
                            "status": "ok" if series.complete else "blocked",
                            "symbol": pair, "requested_timeframe": timeframe,
                            "provider_timeframe": normalize_timeframe(timeframe),
                            "source": "direct",
                            "candles": [c.model_dump(mode="json") for c in series.candles],
                            "metadata": {"usable_count": len(series.candles),
                                         "is_sufficient": series.complete},
                            "blocking_reasons": [] if series.complete else ["insufficient_usable_candles"],
                            "warnings": [],
                        }
                        timeframe_candles[timeframe] = canonical.pop("candles", [])
                        canonical.pop("forming_candle", None)
                        timeframe_results[timeframe] = canonical
                        if canonical["blocking_reasons"]:
                            timeframe_failures.extend(
                                f"{pair.lower()}_candles_{timeframe}_{reason}"
                                for reason in canonical["blocking_reasons"]
                            )
                        if timeframe == "1d": d1 = series
                        elif timeframe == "4h": h4 = series
                        elif timeframe == "1h": h1 = series
                        else: m15 = series
                    if timeframe_failures:
                        failed_metadata = [
                            value.get("metadata", {}) for value in timeframe_results.values()
                        ]
                        candle_request_summary = {
                            "upstream_requests": sum(int(item.get("attempts") or 0)
                                                     for item in failed_metadata),
                            "cache_hits": sum(bool(item.get("cache_hit"))
                                              for item in failed_metadata),
                            "coalesced_requests": sum(int(item.get("coalesced_requests") or 0)
                                                      for item in failed_metadata),
                            "rate_limit_retries": sum(int(item.get("retry_count") or 0)
                                                      for item in failed_metadata),
                            "total_backoff_seconds": round(sum(
                                float(item.get("total_backoff_seconds") or 0)
                                for item in failed_metadata
                            ), 3),
                            "cooldown_until": max((
                                str(item["cooldown_until"]) for item in failed_metadata
                                if item.get("cooldown_until")
                            ), default=None),
                        }
                        raise AutonomousExecutionError(
                            "market_snapshot_unavailable",
                            "One or more required TradeLocker candle timeframes failed validation.",
                            reasons=timeframe_failures,
                            details={"missing_component": "candle_history",
                                     "failing_timeframes": [key for key, value in timeframe_results.items()
                                                            if value["blocking_reasons"]],
                                     "timeframes": timeframe_results,
                                     "candle_requests": candle_request_summary},
                        )
                market[pair] = {"quote": quote,"bid":bid,"ask":ask,"spread":ask-bid,"quote_retrieved_at":utcnow().isoformat(),
                    "instrument_metadata":instrument.__dict__,
                    "candles_1d": timeframe_candles.get("1d", []),
                    "candles_4h": timeframe_candles.get("4h", []),
                    "candles_1h": timeframe_candles.get("1h", []),
                    "candles_15m": timeframe_candles.get("15m", []),
                    "timeframes": timeframe_results,
                    "complete": True}
        finally:
            await client.aclose()
        now, snapshot_id = utcnow(), f"snap_{uuid4().hex}"
        daily_pnl = account.today.net + account.open_net_pnl
        equity = account.projected_balance
        high_watermark = self.execution.observe_equity(
            user_sub, context.connection_id, context.account_id, context.acc_num, equity
        )
        classified = classify_orders(positions, orders)
        open_positions = count_open_positions(positions)
        pending_entries = classified["counts"]["pending_entry"]
        risk_state = {
            "daily_realized_pnl": account.today.net, "open_pnl": account.open_net_pnl,
            "daily_loss_remaining": 0.0,
            "maximum_new_trade_risk": 0.0,
            "current_drawdown_percent": max(0.0, (high_watermark - equity) / high_watermark * 100) if high_watermark > 0 else 100.0,
            "open_positions": open_positions, "maximum_open_positions": context.risk["maximum_open_positions"],
            "pending_entry_orders": pending_entries, "maximum_pending_entry_orders": context.risk["maximum_pending_orders"],
            "protective_stop_orders": classified["counts"]["protective_stop_loss"],
            "protective_take_profit_orders": classified["counts"]["protective_take_profit"],
            "total_open_risk_pct": None,
            "can_open_position": open_positions < context.risk["maximum_open_positions"] and pending_entries < context.risk["maximum_pending_orders"],
            "blocking_reasons": (["maximum_open_positions_reached"]
                if open_positions >= context.risk["maximum_open_positions"] else [])
                + (["maximum_pending_orders_reached"]
                if pending_entries >= context.risk["maximum_pending_orders"] else []),
            "order_classification": classified,
        }
        ownership=self.execution.profile_broker_ids(user_sub,context.profile_ref)
        metrics=profile_attributed_capital_metrics(positions,orders,owned_position_ids=ownership["position_ids"],owned_order_ids=ownership["order_ids"],order_history=order_history)
        metrics["open_risk_amount"]=max(metrics["open_risk_amount"],ownership["estimated_open_risk"])
        metrics["margin_used"]=max(metrics["margin_used"],ownership["estimated_margin_used"])
        capital_state=build_capital_state(account_equity=equity,allocation=context.risk["capital_allocation"],
            risk_policy={"maximum_total_open_risk_pct":context.risk.get("maximum_total_open_risk_pct",1)},**metrics)
        try:self.brokers.validate_profile_capital(user_sub,context.profile_ref,equity)
        except BrokerStorageError:risk_state["blocking_reasons"].append("account_capital_overallocated")
        risk_state["can_open_position"]=risk_state["can_open_position"] and not risk_state["blocking_reasons"]
        risk_state["capital_state"]=capital_state
        profile_pnl=metrics["realized_profile_pnl"]+metrics["unrealized_profile_pnl"]
        risk_state["daily_loss_remaining"]=max(0.0,capital_state["risk_base_amount"]*context.risk["daily_loss_limit_percent"]/100+profile_pnl)
        risk_state["maximum_new_trade_risk"]=capital_state["risk_base_amount"]*context.risk["risk_per_trade_percent"]/100
        candle_metadata = [value.get("metadata", {}) for pair_data in market.values()
                           for value in pair_data.get("timeframes", {}).values()]
        candle_request_summary = {
            "upstream_requests": sum(int(item.get("attempts") or 0) for item in candle_metadata),
            "cache_hits": sum(bool(item.get("cache_hit")) for item in candle_metadata),
            "coalesced_requests": sum(int(item.get("coalesced_requests") or 0) for item in candle_metadata),
            "rate_limit_retries": sum(int(item.get("retry_count") or 0) for item in candle_metadata),
            "total_backoff_seconds": round(sum(float(item.get("total_backoff_seconds") or 0)
                                                   for item in candle_metadata), 3),
            "cooldown_until": max((str(item["cooldown_until"]) for item in candle_metadata
                                   if item.get("cooldown_until")), default=None),
        }
        result = {
            "schema_version": "1.0", "status": "ok", "snapshot_id": snapshot_id,
            "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(seconds=settings.autonomous_snapshot_ttl_seconds)).isoformat(),
            "account": account_json, "positions": positions, "pending_orders": orders,
            "recent_order_history": order_history[-100:],
            "profile_ref":context.profile_ref,"account_ref":context.account_record_id,"connection_ref":context.connection_ref,
            "account_alias":context.account_alias,"confirmed_demo":context.demo_classification=="demo","kill_switch":self._kill_switch(user_sub),
            "risk_state": risk_state, "strategy": {"name": context.risk["strategy_name"], "version": context.risk["strategy_version"]},
            "providers": providers, "news_blackouts": blackouts,
            "provider_context": provider_context, "warnings": list(dict.fromkeys(warnings)),
            "candle_requests": candle_request_summary,
            "market": {"pairs": market}, "execution_eligibility":
                (not finnhub_required or (not blackouts and providers["finnhub"]["available"] and not providers["finnhub"]["stale"]))
                and (not fred_required or (providers["fred"]["available"] and not providers["fred"]["stale"]))
                and risk_state["can_open_position"],
        }
        self.execution.insert_snapshot({
            "id": snapshot_id, "user_sub": user_sub, "connection_id": context.connection_id,
            "account_id": context.account_id, "acc_num": context.acc_num, "environment": context.environment,
            "strategy_name": context.risk["strategy_name"], "strategy_version": context.risk["strategy_version"],
            "normalized_snapshot_json": json.dumps(result, separators=(",", ":"), sort_keys=True),
            "retrieved_at": now.isoformat(), "expires_at": result["expires_at"], "created_at": now.isoformat(),
        })
        logger.info(
            "autonomous_demo snapshot_created user_id=%s connection_ref=%s account_ref=%s account_alias=%s environment=demo mode=%s snapshot_id=%s kill_switch=%s finnhub_available=%s fred_available=%s",
            user_sub, context.connection_ref, context.account_record_id, context.account_alias,
            context.execution_mode.value, snapshot_id, self._kill_switch(user_sub),
            providers["finnhub"]["available"], providers["fred"]["available"],
        )
        return result

    @staticmethod
    def _quote(quote: Any) -> tuple[float, float]:
        bid = _find_value(quote, ("bid", "bidPrice", "bp", "b"))
        ask = _find_value(quote, ("ask", "askPrice", "ap", "a"))
        return _positive(bid, "bid quote"), _positive(ask, "ask quote")

    @classmethod
    def _conversion_rate(cls, from_currency: str, to_currency: str, market_pairs: dict[str, Any]) -> float:
        if from_currency == to_currency:
            return 1.0
        direct, inverse = f"{from_currency}{to_currency}", f"{to_currency}{from_currency}"
        if direct in market_pairs:
            bid, ask = cls._quote(market_pairs[direct]["quote"])
            return (bid + ask) / 2
        if inverse in market_pairs:
            bid, ask = cls._quote(market_pairs[inverse]["quote"])
            return 1 / ((bid + ask) / 2)
        raise AutonomousExecutionError("position_size_unverifiable", "A verified currency-conversion quote is unavailable.", status="rejected")

    @classmethod
    async def _fresh_conversion_rate(cls, client: TradeLockerClient, from_currency: str, to_currency: str) -> float:
        if from_currency == to_currency:
            return 1.0
        for symbol, inverse in ((f"{from_currency}{to_currency}", False), (f"{to_currency}{from_currency}", True)):
            try:
                bid, ask = cls._quote(await client.get_quote(symbol))
                midpoint = (bid + ask) / 2
                return 1 / midpoint if inverse else midpoint
            except TradeLockerError as exc:
                if exc.code != "symbol_not_found":
                    raise
        raise AutonomousExecutionError("position_size_unverifiable", "A fresh currency-conversion quote is unavailable.", status="rejected")

    async def review(self, user_sub: str, profile_ref: str, proposal: AutonomousOrderProposal, *, autonomous: bool=False) -> dict[str, Any]:
        context = await self.context(user_sub, profile_ref, allow_autonomous=autonomous)
        if autonomous:
            controls=self.execution.get_autonomous_controls(user_sub)
            if controls["global_autonomous_kill_switch"]:
                raise AutonomousExecutionError("global_autonomous_kill_switch_enabled", "The global autonomous kill switch blocks new autonomous previews.")
            if not controls["demo_autonomous_enabled"]:
                raise AutonomousExecutionError("demo_autonomous_disabled", "Demo autonomous trading is disabled.")
        snapshot = self.execution.get_snapshot(proposal.snapshot_id)
        if not snapshot or snapshot["user_sub"] != user_sub:
            raise AutonomousExecutionError("snapshot_not_found", "The snapshot is unavailable.", status="rejected")
        if snapshot["account_id"] != context.account_id or snapshot["acc_num"] != context.acc_num or snapshot["environment"] != "demo":
            raise AutonomousExecutionError("snapshot_account_mismatch", "The snapshot does not belong to the selected demo account.", status="rejected")
        if datetime.fromisoformat(snapshot["expires_at"]) <= utcnow():
            raise AutonomousExecutionError("snapshot_expired", "The snapshot has expired.", status="rejected")
        pair = normalize_pair(proposal.pair)
        violations = []
        if pair not in context.risk["allowed_pairs"]:
            violations.append("pair_not_allowed")
        if self.execution.has_active_preview(user_sub, context.account_id, context.acc_num, pair, utcnow().isoformat()):
            violations.append("duplicate_setup")
        if (proposal.side == "long" and not (proposal.stop_loss < proposal.entry < proposal.take_profit)) or (proposal.side == "short" and not (proposal.take_profit < proposal.entry < proposal.stop_loss)):
            violations.append("invalid_protective_prices")
        normalized = snapshot["normalized_snapshot"]
        warnings = list(normalized.get("warnings", []))
        finnhub = normalized.get("providers", {}).get("finnhub", {})
        fred = normalized.get("providers", {}).get("fred", {})
        if (not finnhub.get("available") or finnhub.get("stale")) and finnhub.get("required"):
            violations.append("provider_unavailable")
        if (not fred.get("available") or fred.get("stale")) and fred.get("required"):
            violations.append("required_macro_provider_unavailable")
        if normalized.get("news_blackouts") and finnhub.get("required"):
            violations.append("news_blackout")
        if not normalized["risk_state"]["can_open_position"]:
            violations.append("position_or_order_limit")
        market = normalized.get("market", {}).get("pairs", {}).get(pair, {})
        if not market.get("complete"):
            violations.append("market_data_incomplete")
        if violations:
            raise AutonomousExecutionError("risk_validation_failed", "The proposed order failed deterministic risk validation.", status="rejected", reasons=violations)
        client = self._client(context.connection, context)
        try:
            instrument_payload = await client.get_instrument_details(pair)
        except TradeLockerError:
            raise AutonomousExecutionError("position_size_unverifiable", "Broker instrument metadata is unavailable.", status="rejected") from None
        finally:
            await client.aclose()
        metadata = parse_instrument_metadata(instrument_payload, pair)
        bid, ask = self._quote(market["quote"])
        calculation = calculate_reward_risk(
            entry=proposal.entry, stop_loss=proposal.stop_loss, take_profit=proposal.take_profit,
            bid=bid, ask=ask, metadata=metadata,
            minimum_reward_risk=context.risk["minimum_reward_risk"], side=proposal.side,
            order_type=proposal.order_type,
        )
        if Decimal(str(calculation["reward_risk"])) < Decimal(str(context.risk["minimum_reward_risk"])):
            violations.append("reward_risk_too_low")
        if Decimal(str(calculation["gross_risk_distance"])) < Decimal(str(metadata.minimum_stop_distance)):
            raise AutonomousExecutionError("broker_stop_distance_invalid", "The stop distance is below the broker minimum.", status="rejected")
        spread = ask - bid
        if spread / metadata.pip_size > settings.autonomous_max_spread_pips:
            raise AutonomousExecutionError("spread_too_wide", "The snapshot spread exceeds the configured maximum.", status="rejected")
        if violations:
            raise AutonomousExecutionError(
                "risk_validation_failed", "The proposed order failed deterministic risk validation.",
                status="rejected", reasons=violations,
                details={"calculation": calculation, "warnings": list(dict.fromkeys(warnings))},
            )
        account_currency = (context.currency or normalized["account"]["account"].get("currency") or "").upper()
        quote_to_account = self._conversion_rate(
            metadata.quote_currency, account_currency,
            normalized.get("market", {}).get("pairs", {}),
        )
        size = calculate_broker_position_size(
            balance=float(normalized["account"]["balance"]), available_funds=float(normalized["account"]["available_funds"]),
            risk_percent=float(context.risk["risk_per_trade_percent"]), entry=proposal.entry,
            stop_loss=proposal.stop_loss, metadata=metadata, quote_to_account_rate=quote_to_account,
            spread=spread,
            commission_per_lot=metadata.commission_per_lot,
            risk_base_amount=float(normalized["risk_state"]["capital_state"]["risk_base_amount"]),
            remaining_allocation_margin=float(normalized["risk_state"]["capital_state"]["remaining_margin_budget"]),
        )
        preview_id, now = f"preview_{uuid4().hex}", utcnow()
        estimated_reward = size["lot_size"] * float(calculation["reward_distance"]) * metadata.contract_size * quote_to_account
        record = {
            "id": preview_id, "snapshot_id": proposal.snapshot_id, "user_sub": user_sub,
            "connection_id": context.connection_id, "account_id": context.account_id, "acc_num": context.acc_num,
            "profile_ref": context.profile_ref, "account_record_id": context.account_record_id,
            "connection_ref": context.connection_ref, "account_alias": context.account_alias,
            "server": context.server, "base_url": context.base_url,
            "demo_classification": context.demo_classification,
            "environment": "demo", "pair": pair, "instrument_id": metadata.instrument_id,
            "route_id": metadata.route_id, "side": proposal.side, "order_type": proposal.order_type,
            "entry": proposal.entry, "stop_loss": proposal.stop_loss, "take_profit": proposal.take_profit,
            "quantity": size["quantity"], "lot_size": size["lot_size"], "estimated_risk": size["estimated_risk"],
            "estimated_margin":size["estimated_margin"],
            "risk_percent": size["risk_percent"], "estimated_reward": estimated_reward, "reward_risk": calculation["reward_risk"],
            "broker_metadata_json": json.dumps(metadata.__dict__, separators=(",", ":"), sort_keys=True),
            "status": "approved", "violations_json": "[]",
            "execution_origin": "autonomous" if autonomous else "manual",
            "expires_at": (now + timedelta(seconds=settings.autonomous_preview_ttl_seconds)).isoformat(), "created_at": now.isoformat(),
        }
        self.execution.insert_preview(record)
        logger.info(
            "autonomous_demo preview_validated user_id=%s connection_ref=%s account_ref=%s account_alias=%s environment=demo mode=%s snapshot_id=%s preview_id=%s instrument=%s side=%s quantity=%s estimated_risk=%.6f result=approved kill_switch=%s",
            user_sub, context.connection_ref, context.account_record_id, context.account_alias,
            context.execution_mode.value, proposal.snapshot_id, preview_id, pair,
            proposal.side, size["lot_size"], size["estimated_risk"], self._kill_switch(user_sub),
        )
        return {"schema_version": "1.0", "status": "approved", "preview_id": preview_id, "snapshot_id": proposal.snapshot_id, "pair": pair, "side": proposal.side, "order_type": proposal.order_type, "entry": proposal.entry, "stop_loss": proposal.stop_loss, "take_profit": proposal.take_profit, **size, "estimated_reward": estimated_reward, "reward_risk": calculation["reward_risk"], "calculation": calculation, "warnings": list(dict.fromkeys(warnings)), "expires_at": record["expires_at"], "violations": []}

    async def submit(self, user_sub: str, preview_id: str, idempotency_key: str, *, before_dispatch=None) -> dict[str, Any]:
        preview = self.execution.get_preview(preview_id)
        if not preview or preview["user_sub"] != user_sub:
            raise AutonomousExecutionError("preview_not_found", "The approved preview is unavailable.", status="rejected")
        fingerprint = hashlib.sha256(json.dumps({key: preview[key] for key in ("id", "account_id", "acc_num", "pair", "side", "order_type", "entry", "stop_loss", "take_profit", "lot_size")}, sort_keys=True).encode()).hexdigest()
        existing_submission = (
            self.execution.get_submission(preview_id=preview_id)
            or self.execution.get_submission(idempotency_key=idempotency_key)
        )
        if existing_submission:
            if (existing_submission.get("preview_id") != preview_id
                    or existing_submission.get("idempotency_key") != idempotency_key
                    or existing_submission.get("request_fingerprint") != fingerprint):
                raise AutonomousExecutionError("idempotency_conflict", "The idempotency key belongs to another request.", status="rejected")
            return {
                **self._submission_result(existing_submission),
                "account_alias": preview.get("account_alias"), "confirmed_demo": True,
                "symbol": preview["pair"],
                "side": "buy" if preview["side"] == "long" else "sell",
                "order_type": preview["order_type"], "quantity": preview["lot_size"],
                "quantity_lots": preview["lot_size"], "quantity_units": preview["quantity"],
                "entry": preview["entry"], "stop_loss": preview["stop_loss"],
                "take_profit": preview["take_profit"],
            }
        context = await self.context(user_sub, preview.get("profile_ref") or "",allow_autonomous=preview.get("execution_origin")=="autonomous")
        routing_fields = ("connection_id","account_id","acc_num","environment","profile_ref","account_record_id","connection_ref","account_alias","server","base_url","demo_classification")
        current = {"connection_id":context.connection_id,"account_id":context.account_id,"acc_num":context.acc_num,"environment":context.environment,
            "profile_ref":context.profile_ref,"account_record_id":context.account_record_id,"connection_ref":context.connection_ref,
            "account_alias":context.account_alias,"server":context.server,"base_url":context.base_url,"demo_classification":context.demo_classification}
        if any(preview.get(key) != current[key] for key in routing_fields):
            logger.warning("execution_routing_mismatch user_ref=%s profile_ref=%s account_ref=%s connection_ref=%s", user_sub, context.profile_ref, context.account_record_id, context.connection_ref)
            raise AutonomousExecutionError("preview_account_mismatch", "The preview routing no longer matches its profile-bound account.", status="rejected")
        if preview["status"] != "approved" or datetime.fromisoformat(preview["expires_at"]) <= utcnow():
            raise AutonomousExecutionError("preview_expired_or_unapproved", "The preview is expired or not approved.", status="rejected")
        snapshot = self.execution.get_snapshot(preview["snapshot_id"])
        if not snapshot or datetime.fromisoformat(snapshot["expires_at"]) <= utcnow():
            raise AutonomousExecutionError("snapshot_expired", "The preview snapshot has expired.", status="rejected")
        submission_id = f"submission_{uuid4().hex}"
        claimed, existing = self.execution.claim_submission(submission_id, preview_id, idempotency_key, fingerprint)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        if not claimed:
            if existing.get("request_fingerprint") != fingerprint:
                raise AutonomousExecutionError("idempotency_conflict", "The idempotency key belongs to another request.", status="rejected")
            return self._submission_result(existing)
        logger.info(
            "autonomous_demo submission_started user_id=%s connection_ref=%s account_ref=%s account_alias=%s environment=demo mode=%s preview_id=%s idempotency_key_hash=%s instrument=%s side=%s quantity=%s kill_switch=%s",
            user_sub, context.connection_ref, context.account_record_id, context.account_alias,
            context.execution_mode.value, preview_id, key_hash, preview["pair"],
            preview["side"], preview["lot_size"], self._kill_switch(user_sub),
        )
        # Forced fresh status and broker state are retrieved after the durable claim.
        dispatched = False
        autonomous_origin = preview.get("execution_origin") == "autonomous"
        try:
            account = await self.account_status_service.retrieve(user_sub, context.account_alias)
            if autonomous_origin:
                controls=self.execution.get_autonomous_controls(user_sub)
                if controls["global_autonomous_kill_switch"]:
                    raise AutonomousExecutionError("global_autonomous_kill_switch_enabled", "The global autonomous kill switch blocks order submission.", status="rejected")
                if not controls["demo_autonomous_enabled"]:
                    raise AutonomousExecutionError("demo_autonomous_disabled", "Demo autonomous trading is disabled.", status="rejected")
            providers, blackouts, _ = await self._provider_state()
            finnhub_required, fred_required = self._provider_requirements(context, autonomous_origin)
            if finnhub_required and (
                not providers["finnhub"]["available"] or providers["finnhub"]["stale"] or blackouts
            ):
                raise AutonomousExecutionError("news_validation_unavailable", "Required news-blackout validation failed closed.", status="rejected")
            if fred_required and (
                not providers["fred"]["available"] or providers["fred"]["stale"]
            ):
                raise AutonomousExecutionError("required_macro_provider_unavailable","Required macro validation failed closed.",status="rejected")
            day_start=utcnow().replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
            if self.execution.new_entries_since(user_sub,context.account_id,day_start)>=context.risk["maximum_new_entries_per_day"]:
                raise AutonomousExecutionError("daily_entry_limit","The profile's maximum new entries per day was reached.",status="rejected")
            total_loss = account.today.net + account.open_net_pnl
            allocation=context.risk.get("capital_allocation")
            if allocation:
                try:self.brokers.validate_profile_capital(user_sub,context.profile_ref,account.projected_balance)
                except BrokerStorageError:
                    raise AutonomousExecutionError("account_capital_overallocated","Enabled profiles exceed account equity allocation.",status="rejected") from None
            else:allocation={"mode":"full_account","risk_base":"account_equity","maximum_margin_utilization_pct":100}
            fresh_authorized=authorized_capital(account_equity=account.projected_balance,allocation=allocation)
            fresh_risk_base=fresh_authorized if allocation.get("risk_base")=="allocated_capital" else account.projected_balance
            if account.balance <= 0 or total_loss <= -(fresh_risk_base * context.risk["daily_loss_limit_percent"] / 100):
                raise AutonomousExecutionError("daily_loss_limit", "The account daily loss limit was reached.", status="rejected")
            high_watermark = self.execution.observe_equity(
                user_sub, context.connection_id, context.account_id, context.acc_num,
                account.projected_balance,
            )
            drawdown = max(0.0, (high_watermark - account.projected_balance) / high_watermark * 100) if high_watermark > 0 else 100.0
            if drawdown >= context.risk["drawdown_cutoff_percent"]:
                raise AutonomousExecutionError("drawdown_cutoff", "The account drawdown cutoff was reached.", status="rejected")
            client = self._client(context.connection, context)
            try:
                quote = await client.get_quote(preview["pair"])
                bid, ask = self._quote(quote)
                current = ask if preview["side"] == "long" else bid
                metadata = InstrumentMetadata(**preview["broker_metadata"])
                if (ask - bid) / metadata.pip_size > settings.autonomous_max_spread_pips:
                    raise AutonomousExecutionError("spread_too_wide", "The current spread exceeds the configured maximum.", status="rejected")
                account_currency = (context.currency or account.account.currency or "").upper()
                conversion = await self._fresh_conversion_rate(client, metadata.quote_currency, account_currency)
                current_risk_per_lot = ((abs(preview["entry"] - preview["stop_loss"]) + (ask - bid)) * metadata.contract_size * conversion + metadata.commission_per_lot)
                if current_risk_per_lot * preview["lot_size"] > fresh_risk_base * context.risk["risk_per_trade_percent"] / 100:
                    raise AutonomousExecutionError("risk_limit_changed", "Fresh market state would exceed the account risk limit.", status="rejected")
                if abs(current - preview["entry"]) / preview["entry"] * 100 > settings.autonomous_price_tolerance_percent:
                    raise AutonomousExecutionError("price_moved", "The current price moved beyond the configured tolerance.", status="rejected")
                config = await client.get_config()
                before_positions, before_orders = await client.get_open_positions(), await client.get_orders()
                mapped_positions = map_configured_rows(config_response=config, data_response=before_positions, config_key="positionsConfig", data_key="positions")
                mapped_orders = map_configured_rows(config_response=config, data_response=before_orders, config_key="ordersConfig", data_key="orders")
                classified = classify_orders(mapped_positions, mapped_orders)
                ownership=self.execution.profile_broker_ids(user_sub,context.profile_ref)
                metrics=profile_attributed_capital_metrics(mapped_positions,mapped_orders,
                    owned_position_ids=ownership["position_ids"],owned_order_ids=ownership["order_ids"])
                metrics["open_risk_amount"]=max(metrics["open_risk_amount"],ownership["estimated_open_risk"])
                metrics["margin_used"]=max(metrics["margin_used"],ownership["estimated_margin_used"])
                fresh_capital=build_capital_state(account_equity=account.projected_balance,
                    allocation=allocation,risk_policy={"maximum_total_open_risk_pct":context.risk.get("maximum_total_open_risk_pct",1)},**metrics)
                if float(preview.get("estimated_margin") or 0)>fresh_capital["remaining_margin_budget"]:
                    raise AutonomousExecutionError("capital_margin_limit","The profile allocation margin budget was reached.",status="rejected")
                if float(preview["estimated_risk"])>fresh_capital["remaining_risk_budget"]:
                    raise AutonomousExecutionError("capital_risk_limit","The profile allocation risk budget was reached.",status="rejected")
                open_positions = count_open_positions(mapped_positions)
                if open_positions >= context.risk["maximum_open_positions"]:
                    raise AutonomousExecutionError("maximum_open_positions_reached", "The maximum open-position limit was reached.", status="rejected")
                if classified["counts"]["pending_entry"] >= context.risk["maximum_pending_orders"]:
                    raise AutonomousExecutionError("maximum_pending_orders_reached", "The maximum pending-entry limit was reached.", status="rejected")
                if any(str(_find_value(row, ("tradableInstrumentId", "instrumentId"))) == preview["instrument_id"] for row in mapped_positions + mapped_orders):
                    raise AutonomousExecutionError("equivalent_order_exists", "An equivalent position or order already exists.", status="rejected")
                if autonomous_origin:
                    controls=self.execution.get_autonomous_controls(user_sub)
                    if controls["global_autonomous_kill_switch"]:
                        raise AutonomousExecutionError("global_autonomous_kill_switch_enabled", "The global autonomous kill switch blocks order submission.", status="rejected")
                    if not controls["demo_autonomous_enabled"]:
                        raise AutonomousExecutionError("demo_autonomous_disabled", "Demo autonomous trading is disabled.", status="rejected")
                correlation_id = f"afd-{preview_id[-20:]}"
                order_payload = {
                    "qty": preview["lot_size"], "routeId": preview["route_id"],
                    "side": "buy" if preview["side"] == "long" else "sell",
                    "validity": "IOC" if preview["order_type"] == "market" else "GTC",
                    "type": preview["order_type"], "tradableInstrumentId": preview["instrument_id"],
                    "price": 0 if preview["order_type"] == "market" else preview["entry"],
                    "stopLoss": preview["stop_loss"], "stopLossType": "absolute",
                    "takeProfit": preview["take_profit"], "takeProfitType": "absolute",
                    "strategyId": correlation_id,
                }
                if preview["order_type"] == "stop":
                    order_payload["stopPrice"] = preview["entry"]
                    order_payload["price"] = 0
                response: Any = None
                ambiguous_submit_error: TradeLockerError | None = None
                try:
                    # The durable claim already exists. Never call place_order again for this preview/key.
                    response = await client.place_order(order_payload)
                    if before_dispatch: before_dispatch()
                    dispatched = True
                except TradeLockerError as exc:
                    ambiguous = (
                        exc.code in {"timeout", "request_failed"}
                        or (exc.code == "http_error" and (exc.status_code or 0) >= 500)
                    )
                    if exc.operation != "place_order" or not ambiguous:
                        raise
                    if before_dispatch: before_dispatch()
                    dispatched = True
                    ambiguous_submit_error = exc
                reconciliation = await self._poll_reconciliation(
                    client, config, preview, response, correlation_id,
                )
                if ambiguous_submit_error is not None and not reconciliation["verified"]:
                    reconciliation["submit_error"] = ambiguous_submit_error.code
                if reconciliation.get("position_found") and not (reconciliation.get("stop_loss_matches") and reconciliation.get("take_profit_matches")):
                    position_id=reconciliation.get("broker_position_id")
                    emergency={"attempted":False,"verified_flat":False}
                    if position_id:
                        emergency["attempted"]=True
                        await client.close_position(position_id,strategy_id=f"afd-emergency-{preview_id[-10:]}")
                        final_positions=map_configured_rows(config_response=config,data_response=await client.get_open_positions(),
                            config_key="positionsConfig",data_key="positions")
                        emergency["verified_flat"]=not any(str(_find_value(row,("positionId","id")))==str(position_id) for row in final_positions)
                    reconciliation["protection_failure"]=True;reconciliation["emergency_close"]=emergency
            finally:
                await client.aclose()
            broker_order_id = str(reconciliation.get("broker_order_id") or _find_value(response, ("orderId", "id")) or "") or None
            state = "protection_failure" if reconciliation.get("protection_failure") else "verified" if reconciliation["verified"] else "unknown"
            self.execution.update_submission(
                submission_id, submission_state=state, broker_order_id=broker_order_id,
                broker_position_id=reconciliation.get("broker_position_id"),
                broker_response_sanitized_json={
                    "order_id": broker_order_id, "accepted": bool(reconciliation["verified"]),
                    "dispatch_uncertain": response is None,
                },
                reconciliation_json=reconciliation, submitted_at=utcnow().isoformat(),
                verified_at=utcnow().isoformat() if reconciliation["verified"] else None,
            )
            self.execution.mark_preview_submitted(preview_id)
            result = self.execution.get_submission(preview_id=preview_id) or {}
            execution_id=self._record_run(context, preview, state, broker_order_id, self._submission_result(result))
            self.execution.update_submission(submission_id,execution_id=execution_id)
            logger.info(
                "autonomous_demo submission_completed user_id=%s connection_ref=%s account_ref=%s account_alias=%s preview_id=%s idempotency_key_hash=%s reconciliation_verified=%s result=%s",
                user_sub, context.connection_ref, context.account_record_id, context.account_alias,
                preview_id, key_hash,
                reconciliation["verified"], state,
            )
            return {**self._submission_result(result),"execution_id":execution_id,"account_alias":context.account_alias,"confirmed_demo":True,
                "symbol":preview["pair"],"side":reconciliation.get("side") or ("buy" if preview["side"]=="long" else "sell"),
                "order_type":preview["order_type"],"quantity":preview["lot_size"],
                "quantity_lots":preview["lot_size"],"quantity_units":preview["quantity"],
                "entry":preview["entry"],"stop_loss":preview["stop_loss"],"take_profit":preview["take_profit"]}
        except AutonomousExecutionError as exc:
            self.execution.update_submission(submission_id, submission_state="rejected", reconciliation_json=exc.as_dict())
            logger.warning(
                "autonomous_demo submission_blocked user_id=%s connection_ref=%s account_ref=%s account_alias=%s preview_id=%s idempotency_key_hash=%s failure_category=%s kill_switch=%s",
                user_sub, context.connection_ref, context.account_record_id, context.account_alias,
                preview_id, key_hash, exc.code, self._kill_switch(user_sub),
            )
            raise
        except (AccountStatusUnavailable, TradeLockerError, TradeLockerMappingError) as exc:
            # A timeout/request failure after dispatch is deliberately unknown and never retried blindly.
            operation = getattr(exc, "operation", "mapping")
            code = getattr(exc, "code", "mapping_unavailable")
            state = "unknown" if dispatched or (operation == "place_order" and code in {"timeout", "request_failed"}) else "rejected"
            self.execution.update_submission(submission_id, submission_state=state, reconciliation_json={"error": code, "manual_review_required": state == "unknown"})
            logger.warning(
                "autonomous_demo submission_failed user_id=%s connection_ref=%s account_ref=%s account_alias=%s preview_id=%s idempotency_key_hash=%s failure_category=%s result=%s",
                user_sub, context.connection_ref, context.account_record_id, context.account_alias,
                preview_id, key_hash, code, state,
            )
            if state == "unknown":
                self.execution.mark_preview_submitted(preview_id)
                stored = self.execution.get_submission(preview_id=preview_id) or {}
                execution_id = stored.get("execution_id")
                if not execution_id:
                    execution_id = self._record_run(
                        context, preview, "unknown", stored.get("broker_order_id"),
                        self._submission_result(stored),
                    )
                    self.execution.update_submission(submission_id, execution_id=execution_id)
                return {
                    **self._submission_result(self.execution.get_submission(preview_id=preview_id) or stored),
                    "execution_id": execution_id, "error": "broker_result_unverified",
                    "message": "TradeLocker accepted or may have accepted the order, but the resulting broker state could not be verified.",
                    "account_alias": context.account_alias, "symbol": preview["pair"],
                }
            raise AutonomousExecutionError("broker_rejected", "TradeLocker rejected the demo order.", status="rejected") from None

    async def _poll_reconciliation(
        self, client: TradeLockerClient, config: Any, preview: dict[str, Any],
        response: Any, correlation_id: str,
    ) -> dict[str, Any]:
        """Read broker state with bounded backoff; this method never submits an order."""
        attempts = settings.autonomous_broker_verification_max_attempts
        delay = settings.autonomous_broker_verification_initial_delay_seconds
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.autonomous_broker_verification_timeout_seconds
        attempts_completed = 0
        latest: dict[str, Any] = {
            "verified": False, "order_found": False, "position_found": False,
            "mapping_verified": False, "correlation_id": correlation_id,
        }
        errors: list[str] = []
        for attempt in range(1, attempts + 1):
            attempts_completed = attempt
            payloads: dict[str, Any] = {}
            for name, read in (
                ("orders", client.get_orders),
                ("positions", client.get_open_positions),
                ("history", client.get_orders_history),
            ):
                try:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    payloads[name] = await asyncio.wait_for(read(), timeout=remaining)
                except asyncio.TimeoutError:
                    errors.append(f"{name}:verification_timeout")
                    payloads[name] = {"d": []}
                except TradeLockerError as exc:
                    errors.append(f"{name}:{exc.code}")
                    payloads[name] = {"d": []}
            try:
                latest = self._reconcile(
                    config, payloads["orders"], payloads["history"], payloads["positions"],
                    preview, response,
                    correlation_id=correlation_id,
                )
                latest["verification_attempts"] = attempt
                if latest["verified"]:
                    return latest
            except TradeLockerMappingError as exc:
                errors.append(getattr(exc, "code", "mapping_unavailable"))
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if attempt < attempts and delay > 0:
                await asyncio.sleep(min(delay, remaining))
                delay = min(delay * 2, settings.autonomous_broker_verification_max_delay_seconds)
        latest["verification_attempts"] = attempts_completed
        if errors:
            latest["verification_errors"] = list(dict.fromkeys(errors))
        return latest

    @staticmethod
    def _reconcile(
        config: Any, orders: Any, history: Any, positions: Any,
        preview: dict[str, Any], response: Any, *, correlation_id: str | None = None,
    ) -> dict[str, Any]:
        mapped_orders: list[dict[str, Any]] = []
        mapped_history: list[dict[str, Any]] = []
        mapped_positions: list[dict[str, Any]] = []
        mapping_failures: list[str] = []
        if isinstance(config, dict) and isinstance(orders, dict):
            try:
                mapped_orders = map_configured_rows(config_response=config, data_response=orders, config_key="ordersConfig", data_key="orders")
            except TradeLockerMappingError:
                mapping_failures.append("orders")
        if isinstance(config, dict) and isinstance(history, dict):
            try:
                mapped_history = map_configured_rows(config_response=config, data_response=history, config_key="ordersHistoryConfig", data_key="ordersHistory")
            except TradeLockerMappingError:
                mapping_failures.append("history")
        if isinstance(config, dict) and isinstance(positions, dict):
            try:
                mapped_positions = map_configured_rows(config_response=config, data_response=positions, config_key="positionsConfig", data_key="positions")
            except TradeLockerMappingError:
                mapping_failures.append("positions")
        response_order_id = _find_value(response, ("orderId", "id"))
        correlation_id = correlation_id or f"afd-{preview.get('id', '')[-20:]}"
        order_candidates = [(row, "pending") for row in mapped_orders] + [(row, "history") for row in mapped_history]
        position_candidates = [(row, "position") for row in mapped_positions]
        candidates = order_candidates + position_candidates
        metadata = preview.get("broker_metadata") or {}
        tick = _decimal(metadata.get("tick_size") or metadata.get("pip_size")) or Decimal("0.00000001")
        lot_step = _decimal(metadata.get("lot_step")) or Decimal("0.00000001")
        contract_size = _decimal(metadata.get("contract_size")) or Decimal("100000")

        def numeric_matches(actual: Any, expected: Any, increment: Decimal) -> bool:
            left, right = _decimal(actual), _decimal(expected)
            if left is None or right is None or increment <= 0:
                return False
            return (left / increment).quantize(Decimal("1"), rounding=ROUND_HALF_UP) == (
                right / increment
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        def instrument_matches(row: dict[str, Any]) -> bool:
            instrument = _find_value(row, ("tradableInstrumentId", "instrumentId"))
            symbol = _find_value(row, ("symbol", "instrument", "name"))
            return ((instrument is not None and str(instrument) == str(preview["instrument_id"])) or
                    (symbol is not None and normalize_pair(str(symbol)) == normalize_pair(preview["pair"])))

        def account_matches(row: dict[str, Any]) -> bool:
            row_account = _find_value(row, ("accountId",))
            row_number = _find_value(row, ("accNum", "accountNumber"))
            return (
                (row_account is None or str(row_account) == str(preview.get("account_id")))
                and (row_number is None or str(row_number) == str(preview.get("acc_num")))
            )

        def quantity_values(row: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
            lots = _decimal(_find_value(row, ("qty", "quantityLots", "lots")))
            units = _decimal(_find_value(row, ("quantityUnits", "units")))
            if lots is None and units is not None and contract_size > 0:
                lots = units / contract_size
            if units is None and lots is not None:
                units = lots * contract_size
            return lots, units

        expected_side = "buy" if preview["side"] == "long" else "sell"
        expected_type = _normalized_order_type(preview.get("order_type"))

        def composite_matches(row: dict[str, Any]) -> bool:
            lots, _ = quantity_values(row)
            actual_type = _normalized_order_type(_find_value(row, ("type", "orderType")))
            entry = _find_value(row, ("stopPrice",)) if expected_type == "stop" else _find_value(row, ("price", "entryPrice", "openPrice"))
            return (
                account_matches(row)
                and instrument_matches(row)
                and _normalized_side(_find_value(row, ("side",))) == expected_side
                and actual_type == expected_type
                and numeric_matches(lots, preview["lot_size"], lot_step)
                and numeric_matches(entry, preview["entry"], tick)
                and numeric_matches(_find_value(row, ("stopLoss",)), preview["stop_loss"], tick)
                and numeric_matches(_find_value(row, ("takeProfit",)), preview["take_profit"], tick)
            )

        selected: tuple[dict[str, Any], str] | None = None
        matching_method: str | None = None
        if response_order_id is not None:
            selected = next((item for item in order_candidates if str(_find_value(item[0], ("orderId", "id"))) == str(response_order_id)), None)
            if selected:
                matching_method = "broker_order_id"
        if selected is None and correlation_id:
            selected = next((item for item in candidates if str(_find_value(item[0], ("strategyId", "clientOrderId", "clientId")) or "") == correlation_id), None)
            if selected:
                matching_method = "correlation_id"
        if selected is None:
            selected = next((item for item in candidates if composite_matches(item[0])), None)
            if selected:
                matching_method = "composite"

        order, source = selected if selected else (None, None)
        position_id = _find_value(order, ("positionId",)) if order else None
        position = (order if source == "position" else next(
            (row for row in mapped_positions if position_id is not None and str(_find_value(row, ("positionId", "id"))) == str(position_id)), None
        ))
        lots, units = quantity_values(order or {})
        side = _normalized_side(_find_value(order, ("side",)))
        stop = _find_value(order, ("stopLoss",))
        target = _find_value(order, ("takeProfit",))
        entry = _find_value(order, ("stopPrice",)) if expected_type == "stop" else _find_value(order, ("price", "entryPrice", "openPrice"))
        actual_type = _normalized_order_type(_find_value(order, ("type", "orderType")))
        broker_status = str(_find_value(order, ("status", "orderStatus")) or "").strip() or None
        status_normalized = (broker_status or "").lower().replace("_", "").replace(" ", "")
        is_open = _string_bool(_find_value(order, ("isOpen",)))
        accepted_status = (
            status_normalized not in {"rejected", "cancelled", "canceled", "expired"}
            and (is_open is not False or source in {"history", "position"})
        )
        identity_match = matching_method in {"broker_order_id", "correlation_id"}
        expected_side = "buy" if preview["side"] == "long" else "sell"
        checks = {
            "account_matches": bool(order) and account_matches(order),
            "instrument_matches": bool(order) and instrument_matches(order),
            "quantity_matches": numeric_matches(lots, preview["lot_size"], lot_step),
            "side_matches": side == expected_side,
            "order_type_matches": (actual_type == expected_type) if actual_type else identity_match,
            "entry_matches": numeric_matches(entry, preview["entry"], tick) if entry is not None else identity_match,
            "stop_loss_matches": numeric_matches(stop, preview["stop_loss"], tick),
            "take_profit_matches": numeric_matches(target, preview["take_profit"], tick),
            "broker_status_accepted": bool(order) and accepted_status,
        }
        matched_order_id = _find_value(order, ("orderId", "id")) if order and source != "position" else None
        return {
            "verified": bool(order) and all(checks.values()),
            "mapping_verified": bool(order), "mapping_failures": mapping_failures,
            "order_found": bool(order) and source != "position", "position_found": bool(position),
            "broker_state_source": source,
            "matching_method": matching_method, "correlation_id": correlation_id,
            "broker_order_id": str(matched_order_id) if matched_order_id is not None else None,
            "broker_position_id": str(_find_value(position, ("positionId", "id"))) if position else None,
            "broker_status": broker_status, "symbol": preview.get("pair"), "side": side or None,
            "order_type": actual_type or expected_type,
            "quantity_lots": float(lots) if lots is not None else None,
            "quantity_units": float(units) if units is not None else None,
            "entry": float(_decimal(entry)) if _decimal(entry) is not None else None,
            "stop_loss": float(_decimal(stop)) if _decimal(stop) is not None else None,
            "take_profit": float(_decimal(target)) if _decimal(target) is not None else None,
            **checks,
        }

    @staticmethod
    def _submission_result(record: dict[str, Any]) -> dict[str, Any]:
        state = record.get("submission_state", "unknown")
        reconciliation = record.get("reconciliation", {})
        return {
            "schema_version": "1.0", "status": "submitted" if state == "verified" else state,
            "execution_id": record.get("execution_id"), "broker_order_id": record.get("broker_order_id"),
            "broker_position_id": record.get("broker_position_id"),
            "broker_status": reconciliation.get("broker_status"),
            "manual_review_required": state == "unknown", "reconciliation": reconciliation,
        }

    def _record_run(self, context: VerifiedDemoContext, preview: dict[str, Any], state: str, broker_order_id: str | None, result: dict[str, Any]) -> str:
        now, run_id = utcnow(), f"run_{uuid4().hex}"
        self.execution.insert_run({"id": run_id, "user_sub": context.user_sub, "connection_id": context.connection_id, "account_id": context.account_id, "acc_num": context.acc_num, "snapshot_id": preview["snapshot_id"], "preview_id": preview["id"], "strategy_name": context.risk["strategy_name"], "strategy_version": context.risk["strategy_version"], "decision": "submit", "selected_pair": preview["pair"], "selected_side": preview["side"], "result_status": state, "broker_order_id": broker_order_id, "result_json": result, "started_at": now.isoformat(), "completed_at": now.isoformat(), "created_at": now.isoformat()})
        return run_id

    async def review_action(self,user_sub:str,profile_ref:str,action_type:str,target_id:str)->dict[str,Any]:
        if action_type not in {"cancel_order","close_position"}:
            raise AutonomousExecutionError("invalid_action","Unsupported demo risk-reduction action.",status="rejected")
        context=await self.context(user_sub,profile_ref)
        client=self._client(context.connection, context)
        try:
            config=await client.get_config()
            payload=await (client.get_orders() if action_type=="cancel_order" else client.get_open_positions())
            rows=map_configured_rows(config_response=config,data_response=payload,
                config_key="ordersConfig" if action_type=="cancel_order" else "positionsConfig",
                data_key="orders" if action_type=="cancel_order" else "positions")
        except (TradeLockerError,TradeLockerMappingError):
            raise AutonomousExecutionError("target_unavailable","The profile-bound broker target could not be verified.",status="rejected") from None
        finally: await client.aclose()
        aliases=("orderId","id") if action_type=="cancel_order" else ("positionId","id")
        target=next((row for row in rows if str(_find_value(row,aliases))==str(target_id)),None)
        if target is None: raise AutonomousExecutionError("target_not_found","The pending order or position was not found on the profile-bound account.",status="rejected")
        now=utcnow();preview_id=f"{'cancel' if action_type=='cancel_order' else 'close'}_{uuid4().hex}"
        record={"id":preview_id,"user_sub":user_sub,"action_type":action_type,"profile_ref":context.profile_ref,
            "account_record_id":context.account_record_id,"connection_ref":context.connection_ref,"connection_id":context.connection_id,
            "account_id":context.account_id,"acc_num":context.acc_num,"account_alias":context.account_alias,"environment":context.environment,
            "server":context.server,"base_url":context.base_url,"demo_classification":context.demo_classification,
            "target_id":str(target_id),"target_json":target,"status":"approved",
            "expires_at":(now+timedelta(seconds=settings.autonomous_preview_ttl_seconds)).isoformat(),"created_at":now.isoformat()}
        self.execution.insert_action_preview(record)
        return {"schema_version":"1.0","status":"approved","preview_id":preview_id,"action":action_type,
            "profile_ref":context.profile_ref,"account_alias":context.account_alias,"target":target,
            "expires_at":record["expires_at"],"submission_allowed":True,"blocking_reasons":[]}

    async def submit_action(self,user_sub:str,preview_id:str,idempotency_key:str,*,before_dispatch=None)->dict[str,Any]:
        preview=self.execution.get_action_preview(preview_id)
        if not preview or preview["user_sub"]!=user_sub: raise AutonomousExecutionError("preview_not_found","The action preview is unavailable.",status="rejected")
        if preview["status"]!="approved" or datetime.fromisoformat(preview["expires_at"])<=utcnow():
            raise AutonomousExecutionError("preview_expired_or_unapproved","The action preview is expired or not approved.",status="rejected")
        context=await self.context(user_sub,preview["profile_ref"])
        current=(context.profile_ref,context.account_record_id,context.connection_ref,context.connection_id,context.account_id,context.acc_num,context.environment,context.server,context.base_url,context.demo_classification)
        stored=tuple(preview[key] for key in ("profile_ref","account_record_id","connection_ref","connection_id","account_id","acc_num","environment","server","base_url","demo_classification"))
        if current!=stored: raise AutonomousExecutionError("preview_account_mismatch","The action preview routing no longer matches its profile-bound demo account.",status="rejected")
        execution_id=f"exec_{uuid4().hex}"
        claimed,existing=self.execution.claim_action(execution_id,preview_id,user_sub,idempotency_key,preview["action_type"],preview["target_id"])
        if not claimed:return self._action_result(existing)
        client=self._client(context.connection, context);dispatched=False
        try:
            config=await client.get_config()
            before=await (client.get_orders() if preview["action_type"]=="cancel_order" else client.get_open_positions())
            rows=map_configured_rows(config_response=config,data_response=before,
                config_key="ordersConfig" if preview["action_type"]=="cancel_order" else "positionsConfig",
                data_key="orders" if preview["action_type"]=="cancel_order" else "positions")
            aliases=("orderId","id") if preview["action_type"]=="cancel_order" else ("positionId","id")
            if not any(str(_find_value(row,aliases))==preview["target_id"] for row in rows):
                raise AutonomousExecutionError("target_not_found","The broker target no longer exists.",status="rejected")
            if before_dispatch: before_dispatch()
            response=await (client.cancel_order(preview["target_id"]) if preview["action_type"]=="cancel_order" else client.close_position(preview["target_id"],strategy_id=f"afd-{preview_id[-20:]}"));dispatched=True
            after=await (client.get_orders() if preview["action_type"]=="cancel_order" else client.get_open_positions())
            after_rows=map_configured_rows(config_response=config,data_response=after,
                config_key="ordersConfig" if preview["action_type"]=="cancel_order" else "positionsConfig",
                data_key="orders" if preview["action_type"]=="cancel_order" else "positions")
            absent=not any(str(_find_value(row,aliases))==preview["target_id"] for row in after_rows)
            reconciliation={"target_absent":absent,"verified":absent,"target_id":preview["target_id"]}
            state="verified" if absent else "unknown"
            self.execution.update_action_execution(execution_id,state=state,broker_response={"accepted":True},reconciliation=reconciliation)
            return self._action_result(self.execution.get_action_execution(user_sub,execution_id) or {})
        except AutonomousExecutionError as exc:
            self.execution.update_action_execution(execution_id,state="rejected",error_category=exc.code,reconciliation=exc.as_dict());raise
        except (TradeLockerError,TradeLockerMappingError) as exc:
            code=getattr(exc,"code","mapping_unavailable");state="unknown" if dispatched or getattr(exc,"operation","") in {"cancel_order","close_position"} else "rejected"
            self.execution.update_action_execution(execution_id,state=state,error_category=code,reconciliation={"manual_review_required":state=="unknown"})
            if state=="unknown":return self._action_result(self.execution.get_action_execution(user_sub,execution_id) or {})
            raise AutonomousExecutionError("broker_rejected","TradeLocker rejected the demo action.",status="rejected") from None
        finally: await client.aclose()

    @staticmethod
    def _action_result(record:dict[str,Any])->dict[str,Any]:
        return {"schema_version":"1.0","status":record.get("state","unknown"),"execution_id":record.get("id"),
            "action":record.get("action_type"),"target_id":record.get("target_id"),"error_category":record.get("error_category"),
            "reconciliation":record.get("reconciliation",{}),"created_at":record.get("created_at"),"completed_at":record.get("completed_at"),
            "manual_review_required":record.get("state")=="unknown"}

    async def _reconcile_unknown_execution(
        self, user_sub: str, run: dict[str, Any], submission: dict[str, Any], preview: dict[str, Any],
    ) -> dict[str, Any]:
        context = await self.context(
            user_sub, preview.get("profile_ref") or "",
            allow_autonomous=preview.get("execution_origin") == "autonomous",
        )
        immutable = (
            context.connection_id, context.account_id, context.acc_num, context.profile_ref,
            context.account_record_id, context.connection_ref, context.account_alias,
            context.environment, context.server, context.base_url, context.demo_classification,
        )
        stored = tuple(preview.get(key) for key in (
            "connection_id", "account_id", "acc_num", "profile_ref", "account_record_id",
            "connection_ref", "account_alias", "environment", "server", "base_url", "demo_classification",
        ))
        if immutable != stored:
            logger.warning("execution_reconciliation_routing_mismatch user_ref=%s execution_id=%s", user_sub, run["id"])
            return run
        client = self._client(context.connection, context)
        try:
            config = await client.get_config()
            correlation_id = f"afd-{preview['id'][-20:]}"
            reconciliation = await self._poll_reconciliation(
                client, config, preview, {"orderId": submission.get("broker_order_id")}, correlation_id,
            )
        except (TradeLockerError, TradeLockerMappingError):
            return run
        finally:
            await client.aclose()
        if not reconciliation["verified"]:
            return run
        broker_order_id = reconciliation.get("broker_order_id") or submission.get("broker_order_id")
        self.execution.update_submission(
            submission["id"], submission_state="verified", broker_order_id=broker_order_id,
            broker_position_id=reconciliation.get("broker_position_id"),
            reconciliation_json=reconciliation, verified_at=utcnow().isoformat(),
        )
        updated_submission = self.execution.get_submission(preview_id=preview["id"]) or submission
        durable_result = self._submission_result(updated_submission)
        self.execution.update_run(
            run["id"], result_status="verified", broker_order_id=broker_order_id,
            result_json=durable_result, completed_at=utcnow().isoformat(),
        )
        logger.info(
            "autonomous_demo late_reconciliation_completed user_id=%s connection_ref=%s account_ref=%s account_alias=%s execution_id=%s broker_order_id=%s",
            user_sub, context.connection_ref, context.account_record_id, context.account_alias,
            run["id"], broker_order_id,
        )
        return self.execution.get_run(user_sub, run["id"]) or run

    async def execution_result(self,user_sub:str,execution_id:str)->dict[str,Any]:
        action=self.execution.get_action_execution(user_sub,execution_id)
        if action:return self._action_result(action)
        run=self.execution.get_run(user_sub,execution_id)
        if not run:raise AutonomousExecutionError("execution_not_found","No owned demo execution was found.",status="not_found")
        if run["result_status"] == "unknown" and run.get("preview_id"):
            submission = self.execution.get_submission_by_execution(run["id"])
            preview = self.execution.get_preview(run["preview_id"])
            if submission and preview and preview.get("user_sub") == user_sub:
                run = await self._reconcile_unknown_execution(user_sub, run, submission, preview)
        status = "submitted" if run["result_status"] == "verified" else run["result_status"]
        return {"schema_version":"1.0","status":status,"execution_id":run["id"],"action":run["decision"],
            "symbol":run.get("selected_pair"),"side":run.get("selected_side"),"broker_order_id":run.get("broker_order_id"),
            "result":run.get("result"),"created_at":run.get("created_at"),"completed_at":run.get("completed_at")}

    async def record_no_trade(self, user_sub: str, profile_ref: str, snapshot_id: str, reason_codes: list[str], pairs: list[str]) -> dict[str, Any]:
        context = await self.context(user_sub, profile_ref, require_mode=False, allow_autonomous=True)
        snapshot = self.execution.get_snapshot(snapshot_id)
        if not snapshot or snapshot["user_sub"] != user_sub or snapshot["account_id"] != context.account_id:
            raise AutonomousExecutionError("snapshot_not_found", "The snapshot is unavailable.", status="rejected")
        if datetime.fromisoformat(snapshot["expires_at"]) <= utcnow():
            raise AutonomousExecutionError("snapshot_expired", "The snapshot has expired.", status="rejected")
        normalized_pairs = [normalize_pair(pair) for pair in pairs]
        if any(pair not in ALLOWED_PAIRS for pair in normalized_pairs):
            raise AutonomousExecutionError("pair_not_allowed", "A no-trade pair is outside the allowed watchlist.", status="rejected")
        now, run_id = utcnow(), f"run_{uuid4().hex}"
        self.execution.insert_run({"id": run_id, "user_sub": user_sub, "connection_id": context.connection_id, "account_id": context.account_id, "acc_num": context.acc_num, "snapshot_id": snapshot_id, "preview_id": None, "strategy_name": context.risk["strategy_name"], "strategy_version": context.risk["strategy_version"], "decision": "no_trade", "selected_pair": None, "selected_side": None, "no_trade_reason_codes_json": reason_codes, "result_status": "no_trade", "result_json": {"pairs_evaluated": normalized_pairs}, "started_at": now.isoformat(), "completed_at": now.isoformat(), "created_at": now.isoformat()})
        return {"schema_version": "1.0", "status": "no_trade", "run_id": run_id, "reason_codes": reason_codes, "pairs_evaluated": normalized_pairs}

    def run_result(self, user_sub: str, run_id: str | None = None) -> dict[str, Any]:
        record = self.execution.get_run(user_sub, run_id)
        if not record:
            raise AutonomousExecutionError("run_not_found", "No autonomous run result was found.", status="not_found")
        return {"schema_version": "1.0", "status": record["result_status"], "run_id": record["id"], "preview_id": record.get("preview_id"), "broker_order_id": record.get("broker_order_id"), "decision": record["decision"], "result": record["result"]}
