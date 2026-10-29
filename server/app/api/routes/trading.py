"""Human-confirmed demo ticket, using the same immutable previews as the assistant."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.platform import OrderCalculationRequest, current_claims, trading_order_calculations
from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.brokers.tradelocker.mapping import TradeLockerMappingError, map_configured_rows
from app.models.autonomous import AutonomousOrderProposal
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError, normalize_pair
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.services.instruments import instrument_mapper, InstrumentMappingError
from app.services.tradelocker.accounts import BrokerAccountResolver, AccountResolutionError
from app.services.trading.order_calculations import _derived_price, _target_price, _round
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository

router = APIRouter(prefix="/api/trading", tags=["trading"])


class TicketPreview(OrderCalculationRequest):
    model_config = ConfigDict(extra="forbid")
    profile_ref: str = Field(min_length=1, max_length=80)


class ConfirmSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=128)
    confirmed: Literal[True]


class ActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_ref: str = Field(min_length=1, max_length=80)
    action: Literal["close_position", "cancel_order"]
    target_id: str = Field(min_length=1, max_length=80)


def ticket_profile(user: str, ref: str, account_id: str | None = None, *, opening: bool = True):
    profile = BrokerRepository().get_profile(user, ref)
    if not profile:
        raise HTTPException(404, "Execution profile not found.")
    if account_id and account_id not in {profile["account_id"], profile["account_alias"]}:
        raise HTTPException(409, "The execution profile belongs to another account.")
    if not profile.get("enabled") or profile.get("execution_mode") != "demo_manual":
        raise HTTPException(409, "Select an enabled Manual Demo execution profile in Settings.")
    if profile.get("account_environment") != "demo" or profile.get("is_demo") != 1:
        raise HTTPException(409, "Ticket execution is available only for a verified demo account.")
    if opening:
        if ExecutionRepository().get_autonomous_controls(user)["global_autonomous_kill_switch"]:
            raise HTTPException(409, "The kill switch is enabled. New orders are blocked.")
        configured = ((profile.get("profile_v2") or {}).get("forex") or {}).get("sessions") or profile.get("allowed_sessions") or []
        if configured and not set(configured).intersection(AutonomousDecisionRunner._session(datetime.now(timezone.utc))):
            raise HTTPException(409, "The current time is outside this profile's permitted trading sessions.")
    return profile


def execution_error(exc: AutonomousExecutionError):
    raise HTTPException(status_code=409, detail=exc.as_dict()) from None


@router.post("/order-previews")
async def preview_ticket(payload: TicketPreview, claims=Depends(current_claims)):
    user = claims["sub"]
    ticket_profile(user, payload.profile_ref, payload.account_id)
    if payload.order_type != "market" or payload.quantity.unit != "lot":
        raise HTTPException(409, "This ticket currently supports market orders sized in lots.")
    if payload.stop_loss is None or payload.take_profit is None:
        raise HTTPException(422, "A stop loss and take profit are required before review.")
    calculation = await trading_order_calculations(payload, claims)
    if calculation["blocking_reasons"] or calculation["quote"]["stale"]:
        raise HTTPException(409, {"message": "Refresh or correct the order before review.", "blocking_reasons": calculation["blocking_reasons"]})
    service = AutonomousDemoService()
    try:
        canonical = instrument_mapper.resolve(payload.instrument_id)
        pair = normalize_pair(canonical.symbol)
        snapshot = await service.snapshot(user, payload.profile_ref, pair)
        bid, ask = service._quote(snapshot["market"]["pairs"][pair]["quote"])
        entry = ask if payload.side == "buy" else bid
        stop = _derived_price(payload.stop_loss.model_dump(), entry, calculation["pip_size"], payload.side, protective=True)
        distance = abs(entry-stop)/calculation["pip_size"] if stop is not None else None
        target = _target_price(payload.take_profit.model_dump(), entry, calculation["pip_size"], payload.side, distance)
        if stop is None or target is None:
            raise HTTPException(422, "A valid stop loss and take profit are required.")
        proposal = AutonomousOrderProposal(snapshot_id=snapshot["snapshot_id"], pair=pair,
            side="long" if payload.side == "buy" else "short", entry=entry,
            stop_loss=_round(stop, calculation["price_precision"]), take_profit=_round(target, calculation["price_precision"]),
            reason_codes=["manual_ticket"])
        approved = await service.review(user, payload.profile_ref, proposal, requested_lots=payload.quantity.value)
        # These frozen values are the actual immutable server preview, not the earlier estimate.
        return {**approved, "account_id": payload.account_id, "profile_ref": payload.profile_ref,
                "environment": "demo", "submission_allowed": True,
                "execution_quote": {"bid":bid,"ask":ask,"timestamp":snapshot.get("retrieved_at")}}
    except AutonomousExecutionError as exc:
        execution_error(exc)
    except InstrumentMappingError:
        raise HTTPException(404, "Instrument not found.") from None


@router.post("/order-submissions")
async def submit_ticket(payload: ConfirmSubmission, claims=Depends(current_claims)):
    user = claims["sub"]
    service = AutonomousDemoService()
    preview = service.execution.get_preview(payload.preview_id)
    if not preview or preview["user_sub"] != user or preview.get("execution_origin") != "manual":
        raise HTTPException(404, "Manual order preview not found.")
    def guard():
        try: ticket_profile(user, preview["profile_ref"], opening=True)
        except HTTPException as exc:
            raise AutonomousExecutionError("execution_authorization_changed", str(exc.detail), status="rejected") from None
    try:
        return await service.submit(user, payload.preview_id, payload.idempotency_key, before_dispatch=guard)
    except AutonomousExecutionError as exc:
        execution_error(exc)


@router.get("/executions/{execution_id}")
async def execution_result(execution_id: str, claims=Depends(current_claims)):
    try:
        return await AutonomousDemoService().execution_result(claims["sub"], execution_id)
    except AutonomousExecutionError as exc:
        execution_error(exc)


@router.post("/action-previews")
async def preview_action(payload: ActionPreview, claims=Depends(current_claims)):
    ticket_profile(claims["sub"], payload.profile_ref, opening=False)
    try:
        return await AutonomousDemoService().review_action(claims["sub"], payload.profile_ref, payload.action, payload.target_id)
    except AutonomousExecutionError as exc:
        execution_error(exc)


@router.post("/action-submissions")
async def submit_action(payload: ConfirmSubmission, claims=Depends(current_claims)):
    service = AutonomousDemoService()
    preview = service.execution.get_action_preview(payload.preview_id)
    if not preview or preview["user_sub"] != claims["sub"]:
        raise HTTPException(404, "Action preview not found.")
    def guard():
        try: ticket_profile(claims["sub"], preview["profile_ref"], opening=False)
        except HTTPException as exc:
            raise AutonomousExecutionError("execution_authorization_changed", str(exc.detail), status="rejected") from None
    try:
        return await service.submit_action(claims["sub"], payload.preview_id, payload.idempotency_key, before_dispatch=guard)
    except AutonomousExecutionError as exc:
        execution_error(exc)


def public_row(row, symbols, kind):
    def field(*keys):
        lowered = {k.lower(): v for k, v in row.items()}
        return next((lowered[k.lower()] for k in keys if lowered.get(k.lower()) is not None), None)
    instrument = str(field("tradableInstrumentId", "instrumentId") or "")
    return {"id": str(field("positionId", "id") if kind == "positions" else field("orderId", "id")),
            "symbol": symbols.get(instrument) or field("symbol", "name") or f"Instrument {instrument}",
            "side": field("side"), "quantity": field("qty", "quantity"), "entry": field("avgPrice", "price", "openPrice"),
            "pnl": field("unrealizedPl", "unrealizedPnL", "openPnl", "profit", "pnl", "realizedPnL"),
            "stop_loss": field("stopLoss"), "take_profit": field("takeProfit"),
            "status": field("status", "state"), "type": field("type", "orderType"),
            "time": field("lastModified", "createdDate", "openDate", "createdAt")}


@router.get("/account-activity")
async def account_activity(account_id: str, view: Literal["positions", "pending", "history"] = "positions", claims=Depends(current_claims)):
    try:
        context = BrokerAccountResolver().resolve(claims["sub"], account_ref=account_id)
        async with TradeLockerClient(base_url=context.base_url, username=context.username, password=context.password,
                server=context.server, account_id=context.account_id, account_number=context.account_number) as client:
            config = await client.get_config()
            catalog = await client.get_symbols()
            symbols = {str(row.get("tradableInstrumentId") or row.get("id")): row.get("name") or row.get("symbol") for row in client._instrument_rows(catalog)}
            key = {"positions": "positions", "pending": "orders", "history": "ordersHistory"}[view]
            data = await {"positions": client.get_open_positions, "pending": client.get_orders, "history": client.get_orders_history}[view]()
            rows = map_configured_rows(config_response=config, data_response=data, config_key=f"{key}Config", data_key=key)
        return {"account_id": context.account_record_id, "environment": context.environment, "source": "TradeLocker",
                "checked_at": datetime.now(timezone.utc).isoformat(), "rows": [public_row(r, symbols, view) for r in rows][-200:]}
    except AccountResolutionError:
        raise HTTPException(404, "Account unavailable.") from None
    except (TradeLockerError, TradeLockerMappingError):
        raise HTTPException(502, "Broker activity could not be retrieved or safely mapped. Try refreshing.") from None
