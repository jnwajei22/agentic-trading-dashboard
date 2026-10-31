from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
import logging
import asyncio
import json
import time
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.config.settings import settings
from app.storage.brokers import BrokerRepository, BrokerStorageError
from app.models.onboarding import (
    SelectedTradeLockerAccount,
    TradeLockerConnectionStatus,
    TradeLockerOnboardingStatus,
)
from app.models.autonomous import ExecutionMode, ExecutionSettingsUpdate
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.services.autonomous.decision import decision_provider_readiness
from app.jobs.autonomous_scheduler import AutonomousScheduleService
from app.storage.schedules import ScheduleRepository, ScheduleStorageError
from app.storage.execution import ExecutionRepository
from app.storage.user_experience import UserExperienceRepository
from app.storage.runtime_settings import RuntimeSettingsRepository
from app.services.tradelocker.config_cache import tradelocker_config_cache
from app.services.tradelocker.catalog_cache import instrument_catalog_cache
from app.models.execution_profile_v2 import CapitalAllocationPatch, ExecutionProfileV2
from app.services.trading_policy import MARKET_GROUPS, normalize_instrument, resolve_universe
from app.services.market_workspace import MarketWorkspaceService
from app.services.trading.order_calculations import calculate_order, forex_terms
from app.providers.registry import provider_registry
from app.services.instruments import InstrumentMappingError, instrument_mapper


router = APIRouter(prefix="/api", tags=["platform"])
logger = logging.getLogger(__name__)


class TradeLockerCredentials(BaseModel):
    base_url: str = Field(
        default_factory=lambda: settings.tradelocker_base_url,
        validation_alias=AliasChoices("base_url", "baseUrl"),
    )
    username: str
    password: str = Field(repr=False)
    server: str
    environment: Literal["demo", "live"] | None = None
    connection_id: str | None = Field(default=None, validation_alias=AliasChoices("connection_id", "connectionId"))
    label: str | None = None
    create_new: bool = Field(default=False, validation_alias=AliasChoices("create_new", "createNew"))


class AccountSelection(BaseModel):
    account_id: str = Field(validation_alias=AliasChoices("account_id", "accountId"))
    account_number: str = Field(
        validation_alias=AliasChoices("account_number", "accountNumber", "accNum")
    )
    connection_id: str | None = Field(default=None, validation_alias=AliasChoices("connection_id", "connectionId"))


class CalculationValue(BaseModel):
    mode: Literal["price", "pips", "reward_multiple", "rr"]
    value: float = Field(gt=0, allow_inf_nan=False)


class CalculationQuantity(BaseModel):
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["lot", "share", "contract", "fractional_share"] = "lot"


class OrderCalculationRequest(BaseModel):
    account_id: str
    instrument_id: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit", "stop"] = "market"
    entry_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    quantity: CalculationQuantity
    stop_loss: CalculationValue | None = None
    take_profit: CalculationValue | None = None


class AccountAliasUpdate(BaseModel):
    alias: str = Field(min_length=1, max_length=64)


class ExecutionProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    account_id: str = Field(validation_alias=AliasChoices("account_id", "accountId"))
    strategy_template_id: str = "strategy_hourly_forex_v1"
    execution_mode: Literal["read_only", "demo_manual", "demo_autonomous", "disabled"] = "read_only"
    risk: dict[str, Any] = Field(default_factory=dict)
    allowed_instruments: list[str] = Field(default_factory=list)
    session_rules: dict[str, Any] = Field(default_factory=dict)
    news_filter_enabled: bool = True


class ExecutionProfileUpdate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=64)
    execution_mode: Literal["read_only", "demo_manual", "demo_autonomous", "disabled"] | None = None
    enabled: bool | None = None
    strategy_template_id: str | None = None
    risk: dict[str, Any] | None = None
    allowed_instruments: list[str] | None = None
    session_rules: dict[str, Any] | None = None
    news_filter_enabled: bool | None = None
    decision_provider: Literal["openai","no_trade"] | None = None
    model_identifier: str | None = Field(default=None,max_length=80)
    minimum_confidence: float | None = Field(default=None,ge=0,le=1)


class ExecutionProfileV2Patch(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version: Literal[2] | None = None
    asset_class:Literal["forex","equities","options"]|None=None
    trading_account:str|None=None
    trading_policy: dict[str, Any] | None = None
    market_universe: dict[str, Any] | None = None
    risk_policy: dict[str, Any] | None = None
    capital_allocation: CapitalAllocationPatch | None = None
    exit_policy: dict[str, Any] | None = None
    schedule_policy: dict[str, Any] | None = None
    provider_capability_requirements:list[str]|None=None
    forex:dict[str,Any]|None=None
    equities:dict[str,Any]|None=None
    options:dict[str,Any]|None=None
    enabled: bool | None = None


class AutonomousControlsUpdate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    global_autonomous_kill_switch: bool | None = None
    demo_autonomous_enabled: bool | None = None
    live_autonomous_enabled: bool | None = None
    live_confirmation: str | None = Field(default=None,max_length=40)
    reason: str | None = Field(default=None,max_length=240)


class AutonomousArmRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    armed_until: str | None = Field(default=None,min_length=20,max_length=40)
    arming_hours: int = Field(default=24,ge=1,le=24)
    decision_provider: Literal["openai","no_trade"] = "no_trade"
    model_identifier: str | None = Field(default=None,min_length=1,max_length=80)
    minimum_confidence: float = Field(default=0.70,ge=0.5,le=1.0)
    allowed_sessions: list[Literal["sydney","tokyo","asia","london","new_york","overlap","london_new_york_overlap"]] = Field(default_factory=lambda:["london","new_york","overlap"],min_length=1,max_length=3)
    schedule_ref: str | None = Field(default=None,max_length=80)
    shadow_mode: bool = True


class AutonomousScheduleRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    timezone: str = Field(default="America/Chicago",min_length=1,max_length=80)
    local_times: list[str] | None = Field(default=None,min_length=1,max_length=24)
    recurrence: dict[str,Any] | None = None
    enabled: bool = True
    maximum_lateness_seconds: int = Field(default=600,ge=30,le=3600)


class UserPreferencesUpdate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    appearance:Literal["light","dark","system"]|None=None
    timezone:str|None=Field(default=None,min_length=1,max_length=80)
    date_format:Literal["locale","month_day_year","day_month_year","year_month_day"]|None=None
    time_format:Literal["locale","12_hour","24_hour"]|None=None
    currency_display:Literal["account","usd"]|None=None
    notifications:dict[str,bool]|None=None


class WatchlistCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=60)
    symbols:list[str]=Field(default_factory=list,max_length=100)


class WatchlistUpdate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str|None=Field(default=None,min_length=1,max_length=60)
    items:list[dict[str,Any]]=Field(default_factory=list,max_length=100)


class AccountNicknameUpdate(BaseModel):
    nickname:str|None=Field(default=None,max_length=64)


async def current_claims() -> dict[str, Any]:
    """Single public workspace. Identity is configured on the server, never by a caller.

    These scopes enable the shared operator; the native tool mode, ownership,
    approval, profile, session and kill-switch checks remain authoritative.
    """
    return {"sub": settings.workspace_owner_sub.strip(), "name": "Shared workspace",
            "scope": "forex:read forex:preview forex:execute"}


def repository() -> BrokerRepository:
    try:
        return BrokerRepository()
    except BrokerStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _profile_with_provider_readiness(profile: dict[str, Any]) -> dict[str, Any]:
    return {**profile, "provider_readiness": decision_provider_readiness(
        profile.get("decision_provider"), profile.get("model_identifier"))}


def _capabilities(profile: dict[str, Any], instruments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    instruments = instruments or []
    return {"supported_trading_policy_modes":["adaptive","preset"],
        "available_presets":[{"id":"hourly_forex","version":"1"}], "available_market_groups":list(MARKET_GROUPS),
        "supported_risk_modes":["fixed","adaptive"],
        "supported_stop_modes":["adaptive_structure","volatility","fixed_distance","fixed_percentage"],
        "supported_take_profit_modes":["reward_to_risk","adaptive_structure","trailing_only","none"],
        "trailing_stop_capability":{"configured":True,"broker_managed":False},
        "partial_exit_capability":{"configured":True,"execution_supported":False},
        "account_asset_classes":sorted({item["asset_class"] for item in instruments}),
        "live_demo_restrictions":{"demo_execution":True,"live_autonomous_execution":False},
        "broker_execution_capabilities":{"market_orders":True,"protective_stop_loss":True,"protective_take_profit":True}}


def _profile_contract(profile: dict[str, Any], instruments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    migration = profile.get("migration_state", "legacy_projected")
    warnings = ([{"code":"legacy_profile_projected","message":"This legacy profile is projected into V2 and will be persisted on its next PATCH."}]
        if migration != "native_v2" else [])
    return {"profile_id":profile["public_id"], "account_id":profile["account_id"], "account_alias":profile["account_alias"],
        "account_number":profile.get("account_number"),"broker_name":profile.get("broker_name"),
        "account_environment":profile.get("account_environment"),"nickname":profile.get("nickname"),
        "profile":profile["profile_v2"], "supported_enum_options":_capabilities(profile, instruments),
        "server_defaults":ExecutionProfileV2().model_dump(mode="json"),
        "field_validation_ranges":{"minimum_confidence":{"minimum":0,"maximum":1},"risk_pct_per_trade":{"exclusive_minimum":0,"maximum":1},
            "daily_loss_limit_pct":{"exclusive_minimum":0,"maximum":3},"drawdown_cutoff_pct":{"exclusive_minimum":0,"maximum":10},
            "capital_equity_percentage":{"exclusive_minimum":0,"maximum":100},"capital_margin_utilization_pct":{"exclusive_minimum":0,"maximum":100}},
        "account_capabilities":_capabilities(profile, instruments), "available_market_groups":list(MARKET_GROUPS),
        "warnings":warnings, "migration":{"state":migration,"legacy_compatibility":True,"legacy_columns_retained":True}}


async def _account_instruments(user_sub: str, account_alias: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context=repository().account_connection_context(user_sub,account_alias)
    if context is None: raise HTTPException(status_code=404,detail="Account not found.")
    try:
        async with _chart_client(context,user_sub) as client:
            payload=await client.get_symbols()
    except TradeLockerError as exc:
        raise HTTPException(status_code=502,detail={"error":exc.code,"message":"The account instrument catalog is unavailable."}) from None
    normalized=[item for row in TradeLockerClient._instrument_rows(payload) if (item:=normalize_instrument(row)) is not None]
    # Broker routing is deliberately retained only inside the service boundary.
    public=[{key:value for key,value in item.items() if not key.startswith("_")} for item in normalized]
    return context,public


@router.get("/me")
async def me(claims: dict = Depends(current_claims)) -> dict:
    repository().ensure_user(claims["sub"], claims.get("email"))
    return {"sub": claims["sub"], "email": claims.get("email")}


@router.get("/providers")
async def providers(claims:dict=Depends(current_claims))->dict:
    enabled=set(RuntimeSettingsRepository().get_all()["enabled_providers"])
    rows=[]
    for provider in provider_registry.all():
        item=provider.public()
        if provider.status=="available" and provider.provider_type not in enabled:item["status"]="disabled"
        rows.append(item)
    return {"providers":rows}


@router.get("/providers/{provider_type}/capabilities")
async def provider_capabilities(provider_type:str,claims:dict=Depends(current_claims))->dict:
    provider=provider_registry.get(provider_type)
    if not provider:raise HTTPException(status_code=404,detail="Provider not found.")
    return {"provider_type":provider_type,"status":provider.status,
        "execution_provider":provider.execution_provider,"capabilities":provider.capabilities.public()}


@router.get("/trading/connections")
async def trading_connections(claims:dict=Depends(current_claims))->dict:
    rows=[]
    for item in repository().list_connections(claims["sub"]):
        rows.append({**item,"provider_type":"tradelocker","authentication_type":"credentials",
            "display_name":item.get("label") or item.get("broker_name"),"roles":["execution","account_data","broker_market_data"]})
    return {"connections":rows}


@router.post("/trading/connections/{provider_type}")
async def create_trading_connection(provider_type:str,claims:dict=Depends(current_claims))->dict:
    provider=provider_registry.get(provider_type)
    if not provider:raise HTTPException(status_code=404,detail="Provider not found.")
    if provider_type!="tradelocker":raise HTTPException(status_code=501,detail=f"{provider.display_name} is {provider.status}.")
    return {"provider_type":"tradelocker","status":"credentials_required","connection_url":"/connect-tradelocker?new=1"}


@router.delete("/trading/connections/{connection_id}")
async def delete_trading_connection(connection_id:str,claims:dict=Depends(current_claims))->dict:
    try:
        if not repository().remove_connection(claims["sub"],connection_id):raise HTTPException(status_code=404,detail="Connection not found.")
    except BrokerStorageError as exc:raise HTTPException(status_code=409,detail=str(exc)) from None
    return {"status":"deleted","connection_id":connection_id}


@router.get("/trading/accounts")
async def trading_accounts(claims:dict=Depends(current_claims))->dict:
    capabilities=provider_registry.require("tradelocker").capabilities.public()
    rows=[]
    for item in repository().list_accounts(claims["sub"]):
        rows.append({**item,"external_account_id":item.get("account_id"),"base_currency":item.get("currency"),
            "provider_type":"tradelocker","asset_classes":capabilities["asset_classes"],
            "capabilities":capabilities,"is_primary":item.get("is_default_analysis",False)})
    return {"accounts":rows}


@router.put("/trading/accounts/{account_id}/primary")
async def primary_trading_account(account_id:str,claims:dict=Depends(current_claims))->dict:
    if not repository().set_default_account(claims["sub"],account_id):raise HTTPException(status_code=404,detail="Account not found.")
    return {"status":"primary","account_id":account_id}


@router.put("/trading/accounts/{account_id}/nickname")
async def nickname_trading_account(account_id:str,payload:AccountNicknameUpdate,claims:dict=Depends(current_claims))->dict:
    if not repository().set_account_nickname(claims["sub"],account_id,payload.nickname):raise HTTPException(status_code=404,detail="Account not found.")
    return {"status":"updated","account_id":account_id,"nickname":payload.nickname}


@router.put("/trading/accounts/{account_id}/enable")
async def enable_trading_account(account_id:str,claims:dict=Depends(current_claims))->dict:
    if not repository().set_account_enabled(claims["sub"],account_id,True):raise HTTPException(status_code=404,detail="Account not found.")
    return {"status":"enabled","account_id":account_id}


@router.get("/trading/accounts/{account_id}/capabilities")
async def trading_account_capabilities(account_id:str,claims:dict=Depends(current_claims))->dict:
    account=next((item for item in repository().list_accounts(claims["sub"]) if item["public_id"]==account_id),None)
    if not account:raise HTTPException(status_code=404,detail="Account not found.")
    return {"account_id":account_id,"provider_type":"tradelocker",
        "capabilities":provider_registry.require("tradelocker").capabilities.public()}


@router.get("/user-preferences")
async def user_preferences(claims:dict=Depends(current_claims))->dict:
    return UserExperienceRepository().preferences(claims["sub"])


@router.patch("/user-preferences")
async def update_user_preferences(payload:UserPreferencesUpdate,claims:dict=Depends(current_claims))->dict:
    return UserExperienceRepository().update_preferences(claims["sub"],payload.model_dump(exclude_none=True))


@router.get("/watchlists")
async def list_watchlists(claims:dict=Depends(current_claims))->dict:
    return {"watchlists":UserExperienceRepository().list_watchlists(claims["sub"])}


@router.post("/watchlists",status_code=201)
async def create_watchlist(payload:WatchlistCreate,claims:dict=Depends(current_claims))->dict:
    try:return UserExperienceRepository().create_watchlist(claims["sub"],payload.name,payload.symbols)
    except Exception as exc:
        if "UNIQUE" in str(exc):raise HTTPException(status_code=409,detail="A watchlist with this name already exists.") from None
        raise


@router.put("/watchlists/{watchlist_id}")
async def update_watchlist(watchlist_id:str,payload:WatchlistUpdate,claims:dict=Depends(current_claims))->dict:
    result=UserExperienceRepository().replace_watchlist(claims["sub"],watchlist_id,name=payload.name,items=payload.items)
    if not result:raise HTTPException(status_code=404,detail="Watchlist not found.")
    return result


@router.delete("/watchlists/{watchlist_id}")
async def delete_watchlist(watchlist_id:str,claims:dict=Depends(current_claims))->dict:
    if not UserExperienceRepository().delete_watchlist(claims["sub"],watchlist_id):
        raise HTTPException(status_code=409,detail="The default Forex Majors watchlist cannot be removed.")
    return {"status":"deleted","watchlist_id":watchlist_id}


async def _market_response(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    service=MarketWorkspaceService()
    try:return await getattr(service,method)(*args,**kwargs)
    finally:await service.aclose()


@router.get("/markets/search")
async def market_search(q:str=Query(min_length=1,max_length=80),limit:int=Query(25,ge=1,le=50),
                        claims:dict=Depends(current_claims))->dict:
    return await _market_response("search",q,limit)


@router.get("/markets/overview")
async def market_overview(symbols:str|None=None,claims:dict=Depends(current_claims))->dict:
    values=[value.strip() for value in (symbols or "").split(",") if value.strip()]
    return await _market_response("overview",values or None)


@router.get("/markets/news")
async def market_news(category:str="general",limit:int=Query(20,ge=1,le=50),
                      claims:dict=Depends(current_claims))->dict:
    return await _market_response("news",category,limit)


@router.get("/markets/calendar")
async def market_calendar(start:date|None=None,end:date|None=None,limit:int=Query(50,ge=1,le=100),
                          instrument:str|None=None,
                          claims:dict=Depends(current_claims))->dict:
    first=start or date.today();last=end or first+timedelta(days=14)
    if last<first or (last-first).days>93:raise HTTPException(status_code=422,detail="Calendar range must be 0 to 93 days.")
    try:return await _market_response("calendar",first,last,limit,instrument)
    except InstrumentMappingError as exc:raise HTTPException(status_code=404,detail=str(exc)) from None


@router.get("/markets/macro")
async def market_macro(claims:dict=Depends(current_claims))->dict:
    return await _market_response("macro")


def _quote_number(payload:Any,names:set[str])->float|None:
    if isinstance(payload,dict):
        for key,value in payload.items():
            if key.casefold() in names:
                try:return float(value)
                except (TypeError,ValueError):pass
        for value in payload.values():
            found=_quote_number(value,names)
            if found is not None:return found
    if isinstance(payload,list):
        for value in payload:
            found=_quote_number(value,names)
            if found is not None:return found
    return None


async def _execution_market_context(user_sub:str,account_alias:str,canonical_id:str)->tuple[dict|None,dict|None]:
    context,instruments=await _account_instruments(user_sub,account_alias)
    canonical=instrument_mapper.resolve(canonical_id);candidates={canonical.symbol.casefold(),canonical.symbol.replace("/","").casefold()}
    match=next((item for item in instruments if candidates.intersection({
        str(item.get("symbol","")).casefold(),str(item.get("broker_symbol","")).casefold()})),None)
    tradability={"account_alias":account_alias,"available":match is not None,
        "currently_tradable":bool(match and match.get("tradeable",match.get("tradable",True))),
        "environment":context.get("environment"),"source":"TradeLocker"}
    if not match:return None,tradability
    symbol=str(match.get("broker_symbol") or match.get("symbol") or canonical.symbol)
    try:
        async with _chart_client(context,user_sub) as client:
            raw=await client.get_quote(symbol)
        bid=_quote_number(raw,{"bid","bidprice","bp"});ask=_quote_number(raw,{"ask","askprice","ap"})
        price=_quote_number(raw,{"price","last","lastprice","mid","close"})
        if price is None and bid is not None and ask is not None:price=(bid+ask)/2
        return ({"price":price,"bid":bid,"ask":ask,"timestamp":datetime.now(timezone.utc).isoformat(),
                 "source":"TradeLocker"} if price is not None else None),tradability
    except (TradeLockerError,HTTPException):return None,tradability


@router.post("/trading/order-calculations")
async def trading_order_calculations(payload:OrderCalculationRequest,claims:dict=Depends(current_claims))->dict:
    """Read-only broker-authoritative ticket calculation; never creates a preview or submits an order."""
    repo=repository();owned=next((item for item in repo.list_accounts(claims["sub"])
        if item["public_id"]==payload.account_id or item["account_alias"].casefold()==payload.account_id.casefold()),None)
    if not owned:raise HTTPException(status_code=404,detail="Trading account not found.")
    context=repo.account_connection_context(claims["sub"],owned["account_alias"])
    if not context:raise HTTPException(status_code=404,detail="Trading account not found.")
    try:canonical=instrument_mapper.resolve(payload.instrument_id)
    except InstrumentMappingError as exc:raise HTTPException(status_code=404,detail=str(exc)) from None
    if canonical.asset_class!="forex":raise HTTPException(status_code=409,detail="Order calculations are currently supported for provider-identified forex instruments.")
    requested=canonical.symbol.replace("/","").casefold()
    try:
        async with _chart_client(context,claims["sub"]) as client:
            symbols=await client.get_symbols();rows=TradeLockerClient._instrument_rows(symbols)
            raw=next((row for row in rows if str(row.get("symbol") or row.get("name") or "").replace("/","").replace("_","").casefold()==requested),None)
            if raw is None:raise HTTPException(status_code=409,detail="The selected account does not expose this instrument.")
            normalized=normalize_instrument(raw)
            if normalized is None:raise HTTPException(status_code=409,detail="Provider instrument metadata is unavailable.")
            quote_raw=await client.get_quote(str(normalized["broker_symbol"]));bid=_quote_number(quote_raw,{"bid","bidprice","bp"});ask=_quote_number(quote_raw,{"ask","askprice","ap"})
            quote_epoch=_quote_number(quote_raw,{"timestamp","time","updatedat","lastupdatetime"})
            if quote_epoch and quote_epoch>10_000_000_000:quote_epoch/=1000
            quote_timestamp=datetime.fromtimestamp(quote_epoch,timezone.utc) if quote_epoch else datetime.now(timezone.utc)
            state=await client.get_account_state_payload()
            equity=_quote_number(state,{"projectedbalance","equity","balance"})
            account_currency=str(owned.get("currency") or "").upper() or None
            quote_currency=str(normalized.get("quote_currency") or canonical.symbol[-3:]).upper()
            rate=1.0 if account_currency==quote_currency else None
            entry=ask if payload.side=="buy" else bid
            base=canonical.symbol[:3].upper()
            if rate is None and account_currency==base and entry:rate=1/entry
            if rate is None and account_currency:
                try:
                    conversion=await client.get_quote(f"{quote_currency}{account_currency}")
                    conversion_bid=_quote_number(conversion,{"bid","bidprice","bp"});conversion_ask=_quote_number(conversion,{"ask","askprice","ap"})
                    if conversion_bid and conversion_ask:rate=(conversion_bid+conversion_ask)/2
                except TradeLockerError:
                    try:
                        conversion=await client.get_quote(f"{account_currency}{quote_currency}")
                        conversion_bid=_quote_number(conversion,{"bid","bidprice","bp"});conversion_ask=_quote_number(conversion,{"ask","askprice","ap"})
                        if conversion_bid and conversion_ask:rate=2/(conversion_bid+conversion_ask)
                    except TradeLockerError:pass
    except HTTPException:raise
    except TradeLockerError as exc:
        cooldown=math.ceil(float(exc.details.get("retry_after_seconds") or 0))
        message=(f"Broker prices are cooling down. Retry in {cooldown} seconds." if exc.status_code==429 and cooldown else "Authoritative broker calculation data is unavailable.")
        raise HTTPException(status_code=503 if exc.status_code==429 else 502,detail={"error":exc.code,"message":message,"retryable":exc.status_code==429}) from None
    metadata={"pip_size":_quote_number(raw,{"pipsize"}),"tick_size":normalized.get("tick_size"),
        "price_precision":_quote_number(raw,{"priceprecision","digits","decimalplaces"}),
        "contract_size":normalized.get("contract_size"),"quantity_step":normalized.get("quantity_increment"),
        "minimum_quantity":normalized.get("minimum_quantity"),"maximum_quantity":normalized.get("maximum_quantity"),
        "minimum_stop_distance":_quote_number(raw,{"minimumstopdistance","minstoplossdistance","stoplevel"}),
        "leverage":_quote_number(raw,{"leverage","effectiveleverage"}),"margin_rate":normalized.get("margin"),
        "quote_currency":quote_currency}
    terms=forex_terms(canonical.symbol,metadata)
    result=calculate_order(terms=terms,side=payload.side,quantity=payload.quantity.value,bid=bid,ask=ask,
        quote_timestamp=quote_timestamp,quote_source="TradeLocker",stop_loss=payload.stop_loss.model_dump() if payload.stop_loss else None,
        take_profit=payload.take_profit.model_dump() if payload.take_profit else None,account_currency=account_currency,
        account_equity=equity,quote_to_account_rate=rate,order_type=payload.order_type,entry_price=payload.entry_price)
    result["instrument"]={**canonical.model_dump(),"quantity_unit":terms.quantity_unit}
    result["capabilities"]={"calculation":True,"preview":bool((owned.get("capabilities") or {}).get("order_preview")),
        "submission":bool((owned.get("capabilities") or {}).get("order_submission"))}
    return result


@router.get("/markets/chart")
async def broker_market_chart(instrument:str,account_alias:str,
                              timeframe:Literal["1m","5m","15m","1H"]="5m",history:bool=True,
                              claims:dict=Depends(current_claims))->dict:
    """Read-only broker chart. Never synthesizes candles or submits trading actions."""
    repo=repository()
    context=repo.account_connection_context(claims["sub"],account_alias)
    if context is None:raise HTTPException(status_code=404,detail="Account not found.")
    try:canonical=instrument_mapper.resolve(instrument)
    except InstrumentMappingError as exc:raise HTTPException(status_code=422,detail=str(exc)) from None
    symbol=canonical.symbol.replace("/","")
    async with _chart_client(context, claims["sub"]) as client:
        return await _broker_chart_snapshot(client, symbol, canonical.canonical_id, timeframe, history)


def _chart_client(context:dict, owner:str)->TradeLockerClient:
    return TradeLockerClient(base_url=context["base_url"],username=context["username"],password=context["password"],
        server=context["server"],account_id=context["account_id"],account_number=context["account_number"],
        cache_user_id=owner,cache_connection_id=context.get("connection_ref"),cache_account_record_id=context.get("account_ref"))


async def _broker_chart_snapshot(client:TradeLockerClient,symbol:str,canonical_id:str,timeframe:str,history:bool)->dict:
    checked_at=datetime.now(timezone.utc)
    candles=[];quote=None;warnings=[]
    try:
        raw=await client.get_quote(symbol)
        bid=_quote_number(raw,{"bid","bidprice","bp"});ask=_quote_number(raw,{"ask","askprice","ap"})
        price=_quote_number(raw,{"price","last","lastprice","mid","close"})
        if price is None and bid is not None and ask is not None:price=(bid+ask)/2
        stamp=_quote_number(raw,{"timestamp","time","updatedat","lastupdatetime"})
        if stamp and stamp>10_000_000_000:stamp/=1000
        quote_time=datetime.fromtimestamp(stamp,timezone.utc).isoformat() if stamp and 0<stamp<=checked_at.timestamp()+60 else None
        if price is not None and math.isfinite(price) and price>0:
            quote={"price":price,"bid":bid if bid is not None and math.isfinite(bid) else None,
                   "ask":ask if ask is not None and math.isfinite(ask) else None,"timestamp":quote_time}
    except (TradeLockerError,ValueError,OverflowError,OSError):
        warnings.append("The broker quote could not be refreshed.")
    try:
        # The short live tail includes the forming bar. A quote-only refresh
        # would move the price marker while leaving the plotted OHLC frozen.
        result=await client.get_candles(symbol,timeframe,120 if history else 3,minimum_usable=1)
        candles=[row.model_dump(mode="json") for row in result.candles[-120:]
                 if all(math.isfinite(value) for value in (row.open,row.high,row.low,row.close))]
        if result.cache_hit and not result.cache_fresh:warnings.append("Showing cached broker candles.")
    except TradeLockerError:
        warnings.append("Broker candle history is temporarily unavailable.")
    if not quote and not candles:raise HTTPException(status_code=502,detail="The broker chart feed is unavailable. Try reconnecting shortly.")
    return {"instrument":canonical_id,"timeframe":timeframe,"source":"TradeLocker",
            "checked_at":datetime.now(timezone.utc).isoformat(),"quote":quote,"candles":candles,"warnings":warnings}


@router.get("/markets/chart/stream")
async def broker_market_chart_stream(request:Request,instrument:str,account_alias:str,
                                     timeframe:Literal["1m","5m","15m","1H"]="5m",
                                     claims:dict=Depends(current_claims)):
    # Check ownership before sending headers; credentials never leave the engine.
    context=repository().account_connection_context(claims["sub"],account_alias)
    if context is None:raise HTTPException(status_code=404,detail="Account not found.")
    try:canonical=instrument_mapper.resolve(instrument)
    except InstrumentMappingError as exc:raise HTTPException(status_code=422,detail=str(exc)) from None
    return StreamingResponse(_broker_chart_events(request,context,claims["sub"],canonical.canonical_id,
        canonical.symbol.replace("/",""),timeframe),media_type="text/event-stream",
        headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})


async def _broker_chart_events(request,context,owner,canonical_id,symbol,timeframe,*,lifetime=90,interval=5):
    """Request-scoped market-data sampling, not a trading worker or a tick subscription.

    Keep auth, instrument resolution and HTTP connections alive across samples.
    Rotate before function deadlines; EventSource reconnects and reloads history.
    """
    deadline=time.monotonic()+lifetime
    count=0
    yield "retry: 3000\n: connected\n\n"
    async with _chart_client(context,owner) as client:
        while time.monotonic()<deadline and not await request.is_disconnected():
            started=time.monotonic()
            try:
                async with asyncio.timeout(min(45 if count==0 else 20,deadline-started)):
                    snapshot=await _broker_chart_snapshot(client,symbol,canonical_id,timeframe,count%12==0)
                snapshot["transport"]="sampled_sse"
                snapshot["sample_ms"]=round((time.monotonic()-started)*1000)
                yield "event: snapshot\ndata: "+json.dumps(snapshot,allow_nan=False)+"\n\n"
                count+=1
            except (HTTPException,TimeoutError):
                yield 'event: feed_error\ndata: {"message":"Broker data is delayed. Retrying automatically."}\n\n'
            # A five-second target cadence includes upstream time, not an extra wait.
            remaining=deadline-time.monotonic()
            if remaining>0:await asyncio.sleep(min(remaining,max(1,interval-(time.monotonic()-started))))
    yield "event: rotate\ndata: {}\n\n"


@router.get("/markets/{canonical_id:path}/summary")
async def market_summary(canonical_id:str,account_alias:str|None=None,claims:dict=Depends(current_claims))->dict:
    try:
        execution_quote=tradability=None
        if account_alias:
            try:execution_quote,tradability=await _execution_market_context(claims["sub"],account_alias,canonical_id)
            except HTTPException:pass
        return await _market_response("summary",canonical_id,execution_quote,tradability)
    except InstrumentMappingError as exc:raise HTTPException(status_code=404,detail=str(exc)) from None


@router.get("/markets/{symbol:path}")
async def market_symbol(symbol:str,claims:dict=Depends(current_claims))->dict:
    return await _market_response("symbol",symbol)


@router.get("/accounts/{account_alias}/tradability/{symbol:path}")
async def account_tradability(account_alias:str,symbol:str,claims:dict=Depends(current_claims))->dict:
    context,instruments=await _account_instruments(claims["sub"],account_alias)
    requested=symbol.casefold(); candidates={requested}
    try:
        canonical=instrument_mapper.resolve(symbol)
        candidates.update({canonical.symbol.casefold(),canonical.symbol.replace("/","").casefold()})
    except InstrumentMappingError:pass
    match=next((item for item in instruments if candidates.intersection({
        str(item.get("symbol","")).casefold(),str(item.get("broker_symbol","")).casefold(),
        str(item.get("id","")).casefold(),str(item.get("instrument_id","")).casefold()})),None)
    return {"account_alias":account_alias,"symbol":symbol,"available":match is not None,
            "currently_tradable":bool(match and match.get("tradeable",match.get("tradable",True))),
            "instrument":match,"environment":context.get("environment"),"source":"TradeLocker"}


@router.get("/broker/status")
async def broker_status(claims: dict = Depends(current_claims)) -> dict:
    return (await validated_onboarding_status(claims["sub"])).model_dump(mode="json")


@router.post("/broker/onboarding-status")
async def onboarding_status(claims: dict = Depends(current_claims)) -> dict:
    return (await validated_onboarding_status(claims["sub"])).model_dump(mode="json")


async def validated_onboarding_status(user_sub: str) -> TradeLockerOnboardingStatus:
    repo = repository()
    connection = repo.get_connection(user_sub)
    if connection is None:
        if repo.list_connections(user_sub):
            return TradeLockerOnboardingStatus(status=TradeLockerConnectionStatus.EXPIRED,connected=False,
                message="Reauthenticate TradeLocker to restore account access.")
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.NOT_CONNECTED,
            connected=False,
        )
    if not repo.connection_needs_discovery(user_sub, connection.connection_ref):
        if not connection.account_id or not connection.account_number:
            return TradeLockerOnboardingStatus(status=TradeLockerConnectionStatus.CONNECTED_NO_ACCOUNT,connected=True)
        stored=repo.status(user_sub)
        selected=stored.get("selected_account")
        return TradeLockerOnboardingStatus(status=TradeLockerConnectionStatus.READY if selected else TradeLockerConnectionStatus.CONNECTED_NO_ACCOUNT,
            connected=True,selected_account=SelectedTradeLockerAccount(account_id=selected["account_id"],account_number=selected["account_number"],
                server=selected["server"],environment=selected["environment"],account_alias=selected.get("account_alias")) if selected else None)
    try:
        async with TradeLockerClient(
            base_url=connection.base_url, username=connection.username,
            password=connection.password, server=connection.server,
            account_id=None, account_number=None,
        ) as client:
            discovered = await client.get_accounts()
    except TradeLockerError as exc:
        expired = exc.code in {"expired", "token_expired", "session_expired"}
        rejected = exc.status_code in {400, 401, 403} or exc.code in {
            "unauthorized", "invalid_credentials", "authentication_failed"
        }
        if expired or rejected:
            repo.mark_reauthentication_required(user_sub,connection.connection_ref)
            return TradeLockerOnboardingStatus(
                status=(TradeLockerConnectionStatus.EXPIRED if expired
                        else TradeLockerConnectionStatus.INVALID_CREDENTIALS),
                connected=False,
                message="Reconnect TradeLocker credentials to continue.",
            )
        logger.warning(
            "TradeLocker route=onboarding_status response_status=%s safe_code=%s",
            exc.status_code, exc.code,
        )
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.UNAVAILABLE,
            connected=False,
            message="TradeLocker connection status is temporarily unavailable.",
            retryable=True,
        )
    repo.sync_accounts(user_sub, connection.connection_ref, discovered)
    if not connection.account_id or not connection.account_number:
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.CONNECTED_NO_ACCOUNT,
            connected=True,
        )
    accounts = discovered.get("accounts", []) if isinstance(discovered, dict) else []
    selected_exists = any(
        str(account.get("accountId")) == connection.account_id
        and str(account.get("accNum")) == connection.account_number
        for account in accounts if isinstance(account, dict)
    )
    if not selected_exists:
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.CONNECTED_NO_ACCOUNT,
            connected=True,
        )
    return TradeLockerOnboardingStatus(
        status=TradeLockerConnectionStatus.READY,
        connected=True,
        selected_account=SelectedTradeLockerAccount(
            account_id=connection.account_id,
            account_number=connection.account_number,
            server=connection.server,
            environment=connection.environment,
        ),
    )


@router.post("/broker/tradelocker/save-credentials")
async def save_credentials(
    payload: TradeLockerCredentials, claims: dict = Depends(current_claims)
):
    request_id = uuid.uuid4().hex
    try:
        async with TradeLockerClient(
            base_url=payload.base_url, username=payload.username,
            password=payload.password, server=payload.server,
            account_id=None, account_number=None,
        ) as client:
            discovered = await client.get_accounts()
    except TradeLockerError as exc:
        return _discovery_error_response(exc, request_id, "save_credentials")
    repo = repository()
    connection = repo.save_connection(
        claims["sub"], base_url=payload.base_url, username=payload.username,
        password=payload.password, server=payload.server, environment=payload.environment,
        email=claims.get("email"), connection_ref=payload.connection_id,
        label=payload.label, create_new=payload.create_new,
    )
    repo.sync_accounts(claims["sub"], connection.connection_ref, discovered)
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return TradeLockerOnboardingStatus(
        status=TradeLockerConnectionStatus.CONNECTED_NO_ACCOUNT,
        connected=True,
    ).model_dump(mode="json")


@router.post("/broker/tradelocker/discover-accounts")
async def discover_accounts(connection_id: str | None = None, claims: dict = Depends(current_claims)):
    repo = repository()
    connection = repo.get_connection(claims["sub"], connection_id)
    if connection is None:
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.NOT_CONNECTED,
            connected=False,
        ).model_dump(mode="json")
    try:
        async with TradeLockerClient(
            base_url=connection.base_url, username=connection.username,
            password=connection.password, server=connection.server,
            account_id=None, account_number=None,
        ) as client:
            discovered = await client.get_accounts()
            repo.sync_accounts(claims["sub"], connection.connection_ref, discovered)
            # Legacy onboarding still needs the broker pair for its immediate selection POST.
            # Durable account listings use /api/broker/accounts and expose only safe IDs.
            return discovered
    except TradeLockerError as exc:
        if exc.status_code in {400,401,403}:
            repo.mark_reauthentication_required(claims["sub"],connection.connection_ref)
        return _discovery_error_response(
            exc, uuid.uuid4().hex, "discover_accounts"
        )


def _discovery_error_response(
    exc: TradeLockerError, request_id: str, route_name: str
) -> JSONResponse:
    rejected = exc.status_code in {400, 401, 403}
    status_code = 401 if rejected else 502
    error = (
        "tradelocker_credentials_rejected"
        if rejected
        else "tradelocker_account_discovery_failed"
    )
    message = (
        "TradeLocker rejected the credentials or server selection."
        if rejected
        else "Unable to retrieve TradeLocker accounts."
    )
    logger.warning(
        "TradeLocker route=%s response_status=%s safe_code=%s request_id=%s",
        route_name, exc.status_code, exc.code, request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "message": message,
            "status": status_code,
            "request_id": request_id,
        },
    )


@router.post("/broker/tradelocker/select-account")
async def select_account(
    payload: AccountSelection, claims: dict = Depends(current_claims)
) -> dict:
    if not repository().select_account(
        claims["sub"], payload.account_id, payload.account_number, payload.connection_id
    ):
        return TradeLockerOnboardingStatus(
            status=TradeLockerConnectionStatus.NOT_CONNECTED,
            connected=False,
        ).model_dump(mode="json")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    connection = repository().get_connection(claims["sub"])
    return TradeLockerOnboardingStatus(
        status=TradeLockerConnectionStatus.READY,
        connected=True,
        selected_account=SelectedTradeLockerAccount(
            account_id=payload.account_id,
            account_number=payload.account_number,
            server=connection.server if connection else "TradeLocker",
            environment=connection.environment if connection else None,
        ),
    ).model_dump(mode="json")


@router.delete("/broker/tradelocker")
async def delete_broker(claims: dict = Depends(current_claims)) -> dict:
    repository().delete_connection(claims["sub"])
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status": "deleted", "provider": "tradelocker"}


@router.get("/broker/connections")
async def list_connections(claims: dict = Depends(current_claims)) -> dict:
    return {"connections": repository().list_connections(claims["sub"])}


@router.get("/broker/accounts")
async def list_accounts(claims: dict = Depends(current_claims)) -> dict:
    accounts=repository().list_accounts(claims["sub"])
    for account in accounts:
        account["profiles"]=[_profile_with_provider_readiness(profile) for profile in account.get("profiles",[])]
    return {"accounts": accounts}


@router.put("/broker/accounts/{account_id}/alias")
async def rename_account(account_id: str, payload: AccountAliasUpdate, claims: dict = Depends(current_claims)) -> dict:
    try:
        changed = repository().rename_account(claims["sub"], account_id, payload.alias)
    except BrokerStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not changed: raise HTTPException(status_code=404, detail="Account not found.")
    return {"status": "updated", "account_id": account_id}


@router.put("/broker/accounts/{account_id}/default")
async def default_account(account_id: str, claims: dict = Depends(current_claims)) -> dict:
    if not repository().set_default_account(claims["sub"], account_id):
        raise HTTPException(status_code=404, detail="Account not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status": "updated", "account_id": account_id}


@router.put("/broker/accounts/{account_id}/disable")
async def disable_account(account_id: str, claims: dict = Depends(current_claims)) -> dict:
    if not repository().set_account_enabled(claims["sub"], account_id, False):
        raise HTTPException(status_code=404, detail="Account not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status": "disabled", "account_id": account_id}


@router.put("/broker/accounts/{account_id}/enable")
async def enable_account(account_id:str,claims:dict=Depends(current_claims))->dict:
    if not repository().set_account_enabled(claims["sub"],account_id,True):
        raise HTTPException(status_code=404,detail="Account not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status":"enabled","account_id":account_id}


@router.put("/broker/connections/{connection_id}/disable")
async def disable_connection(connection_id: str, claims: dict = Depends(current_claims)) -> dict:
    if not repository().disable_connection(claims["sub"], connection_id):
        raise HTTPException(status_code=404, detail="Connection not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status": "disabled", "connection_id": connection_id}


@router.delete("/broker/connections/{connection_id}")
async def remove_connection(connection_id:str,claims:dict=Depends(current_claims))->dict:
    try:removed=repository().remove_connection(claims["sub"],connection_id)
    except BrokerStorageError as exc:raise HTTPException(status_code=409,detail=str(exc)) from None
    if not removed:raise HTTPException(status_code=404,detail="Trading connection not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return {"status":"removed","connection_id":connection_id}


@router.get("/execution-profiles")
async def list_execution_profiles(claims: dict = Depends(current_claims)) -> dict:
    return {"profiles": [_profile_with_provider_readiness(profile) for profile in repository().list_profiles(claims["sub"])]}


@router.get("/execution-profiles/{profile_id}")
async def get_execution_profile_v2(profile_id:str,claims:dict=Depends(current_claims))->dict:
    profile=repository().get_profile(claims["sub"],profile_id)
    if not profile:raise HTTPException(status_code=404,detail="Profile not found.")
    return _profile_contract(profile)


@router.patch("/execution-profiles/{profile_id}")
async def patch_execution_profile_v2(profile_id:str,payload:ExecutionProfileV2Patch,claims:dict=Depends(current_claims))->dict:
    patch=payload.model_dump(exclude_none=True)
    try: profile=repository().update_profile_v2(claims["sub"],profile_id,patch)
    except ValidationError as exc:
        raise HTTPException(status_code=422,detail={"error":"profile_validation_failed","fields":exc.errors(include_url=False)}) from None
    if not profile:raise HTTPException(status_code=404,detail="Profile not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    return _profile_contract(profile)


@router.get("/execution-profiles/{profile_id}/capabilities")
async def execution_profile_capabilities(profile_id:str,claims:dict=Depends(current_claims))->dict:
    profile=repository().get_profile(claims["sub"],profile_id)
    if not profile:raise HTTPException(status_code=404,detail="Profile not found.")
    _,instruments=await _account_instruments(claims["sub"],profile["account_alias"])
    return _capabilities(profile,instruments)


@router.get("/autonomous-runs/{run_id}/audit")
async def autonomous_run_audit(run_id:str,claims:dict=Depends(current_claims))->dict:
    record=ExecutionRepository().get_decision_run(claims["sub"],run_id)
    if not record:raise HTTPException(status_code=404,detail="Autonomous run not found.")
    context=record.get("context") or {};market=context.get("market") or {}
    compact_market={symbol:{key:value for key,value in data.items() if key!="timeframes"}
                    for symbol,data in market.items() if isinstance(data,dict)}
    return {"run_id":run_id,"outcome":AutonomousDecisionRunner._public_run(record)["outcome"],
        "selected_market_universe":context.get("allowed_symbols",[]),"markets_currently_open":record.get("validation",{}).get("markets_currently_open",[]),
        "candidates_screened":record.get("validation",{}).get("candidates_screened",0),
        "candidates_deeply_analyzed":record.get("validation",{}).get("candidates_deeply_analyzed",0),
        "chosen_instrument":(record.get("decision") or {}).get("symbol"),"trading_policy":context.get("strategy"),
        "risk_state_counts":context.get("risk_state",{}),"execution_result":record.get("execution",{}),
        "reason_codes":record.get("reason_codes",[]),"market_summary":compact_market,"usage":record.get("usage",{})}


@router.get("/accounts/{account_alias}/instruments")
async def account_instruments(account_alias:str,asset_class:str|None=None,group:str|None=None,
                              tradable:bool|None=None,search:str|None=None,claims:dict=Depends(current_claims))->dict:
    context,instruments=await _account_instruments(claims["sub"],account_alias)
    if asset_class:instruments=[item for item in instruments if item["asset_class"]==asset_class.lower()]
    if group:instruments=[item for item in instruments if item["market_group"]==group]
    if tradable is not None:instruments=[item for item in instruments if item["currently_tradable"] is tradable]
    if search:
        needle=search.casefold();instruments=[item for item in instruments if needle in f"{item['broker_symbol']} {item.get('description') or ''}".casefold()]
    return {"account_alias":context["account_alias"],"instruments":instruments,"count":len(instruments)}


@router.get("/accounts/{account_alias}/market-groups")
async def account_market_groups(account_alias:str,claims:dict=Depends(current_claims))->dict:
    _,instruments=await _account_instruments(claims["sub"],account_alias);counts={group:0 for group in MARKET_GROUPS}
    for item in instruments:counts[item["market_group"]]+=1
    return {"account_alias":account_alias,"groups":[{"id":group,"instrument_count":counts[group]} for group in MARKET_GROUPS]}


@router.get("/accounts/{account_alias}/market-universe")
async def account_market_universe(account_alias:str,profile_id:str|None=None,claims:dict=Depends(current_claims))->dict:
    _,instruments=await _account_instruments(claims["sub"],account_alias)
    profile=repository().get_profile(claims["sub"],profile_id) if profile_id else None
    if profile_id and (not profile or profile["account_alias"].casefold()!=account_alias.casefold()):
        raise HTTPException(status_code=404,detail="Profile not found for this account.")
    universe=profile["profile_v2"]["market_universe"] if profile else {"mode":"all_available"}
    selected=resolve_universe(instruments,universe)
    return {"account_alias":account_alias,"selection":universe,"instruments":selected,"count":len(selected),
        "tradable_count":sum(1 for item in selected if item["currently_tradable"])}


@router.post("/execution-profiles", status_code=201)
async def create_execution_profile(payload: ExecutionProfileCreate, claims: dict = Depends(current_claims)) -> dict:
    try:
        return repository().create_profile(claims["sub"], name=payload.name, account_ref=payload.account_id,
            strategy_template_id=payload.strategy_template_id, execution_mode=payload.execution_mode,
            risk=payload.risk, allowed_instruments=payload.allowed_instruments,
            session_rules=payload.session_rules, news_filter_enabled=payload.news_filter_enabled)
    except BrokerStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.put("/execution-profiles/{profile_id}")
async def update_execution_profile(profile_id: str, payload: ExecutionProfileUpdate, claims: dict = Depends(current_claims)) -> dict:
    try:
        changed = repository().update_profile(claims["sub"], profile_id, name=payload.name,
            execution_mode=payload.execution_mode, enabled=payload.enabled,
            strategy_template_id=payload.strategy_template_id,risk=payload.risk,
            allowed_instruments=payload.allowed_instruments,session_rules=payload.session_rules,
            news_filter_enabled=payload.news_filter_enabled,decision_provider=payload.decision_provider,
            model_identifier=payload.model_identifier,minimum_confidence=payload.minimum_confidence)
    except BrokerStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not changed: raise HTTPException(status_code=404, detail="Profile not found.")
    tradelocker_config_cache.invalidate_user(claims["sub"])
    instrument_catalog_cache.invalidate_user(claims["sub"])
    profile=next(item for item in repository().list_profiles(claims["sub"]) if item["public_id"]==profile_id)
    return {"status":"updated","profile_id":profile_id,"profile":_profile_with_provider_readiness(profile),
        "warnings":[{"code":"legacy_profile_update_deprecated","message":"Use PATCH with the Execution Profile V2 contract."}]}


@router.delete("/execution-profiles/{profile_id}")
async def delete_execution_profile(profile_id: str, confirmation_name: str, claims: dict = Depends(current_claims)) -> dict:
    profile=next((item for item in repository().list_profiles(claims["sub"]) if item["public_id"]==profile_id),None)
    if not profile:raise HTTPException(status_code=404, detail="Profile not found.")
    if confirmation_name!=profile["name"]:
        raise HTTPException(status_code=409,detail={"error":"profile_name_confirmation_required",
            "message":"Type the exact profile name to delete this profile."})
    ScheduleRepository().disable_profile_schedule(claims["sub"],profile_id)
    if not repository().delete_profile(claims["sub"],profile_id):
        raise HTTPException(status_code=404, detail="Profile not found.")
    return {"status":"deleted","profile_id":profile_id}


@router.post("/execution-profiles/{profile_id}/autonomy/arm")
async def arm_autonomous_profile(profile_id:str,payload:AutonomousArmRequest,claims:dict=Depends(current_claims))->dict:
    raise HTTPException(status_code=410,detail={"error":"arming_deprecated",
        "message":"Timed arming is deprecated. Use the durable Demo Autonomous Trading control."})


@router.post("/execution-profiles/{profile_id}/autonomy/disarm")
async def disarm_autonomous_profile(profile_id:str,claims:dict=Depends(current_claims))->dict:
    raise HTTPException(status_code=410,detail={"error":"arming_deprecated",
        "message":"Timed disarming is deprecated. Disable the environment toggle or the profile."})


@router.get("/autonomous-controls")
async def get_autonomous_controls(claims:dict=Depends(current_claims))->dict:
    return ExecutionRepository().get_autonomous_controls(claims["sub"])


@router.patch("/autonomous-controls")
async def patch_autonomous_controls(payload:AutonomousControlsUpdate,claims:dict=Depends(current_claims))->dict:
    changes={key:value for key,value in payload.model_dump().items()
        if key in {"global_autonomous_kill_switch","demo_autonomous_enabled","live_autonomous_enabled"}
        and value is not None}
    if not changes:raise HTTPException(status_code=422,detail="At least one autonomous control is required.")
    if changes.get("live_autonomous_enabled") is True and payload.live_confirmation!="ENABLE LIVE AUTONOMY":
        raise HTTPException(status_code=409,detail={"error":"live_confirmation_required",
            "message":"Type ENABLE LIVE AUTONOMY to enable live autonomous trading."})
    try:return ExecutionRepository().update_autonomous_controls(claims["sub"],changes,
        updated_by=claims["sub"],source="dashboard",reason=payload.reason)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc)) from None


@router.get("/autonomous-controls/audit")
async def autonomous_control_audit(claims:dict=Depends(current_claims))->dict:
    events=ExecutionRepository().autonomous_control_audit(claims["sub"])
    return {"events":[{**event,"occurred_at":event.get("changed_at")} for event in events]}


@router.get("/execution-profiles/{profile_id}/autonomy/status")
async def autonomous_profile_status(profile_id:str,claims:dict=Depends(current_claims))->dict:
    try:return await AutonomousDecisionRunner().status(claims["sub"],profile_id)
    except AutonomousExecutionError as exc:raise HTTPException(status_code=409,detail=exc.as_dict()) from None


@router.post("/execution-profiles/{profile_id}/demo-test")
async def run_profile_demo_test(profile_id:str,claims:dict=Depends(current_claims))->dict:
    profile=repository().get_profile(claims["sub"],profile_id)
    if not profile:raise HTTPException(status_code=404,detail="Strategy not found.")
    if profile.get("account_environment")!="demo":
        raise HTTPException(status_code=409,detail={"error":"demo_account_required",
            "message":"Run Demo Test requires a verified demo trading account."})
    try:
        result=await AutonomousDecisionRunner().run(claims["sub"],profile_id,
            f"demo-test:{profile_id}:{uuid.uuid4().hex[:16]}","demo_test",dry_run=True)
        return {**result,"dry_run":True,"submission_allowed":False,
            "message":"Analysis completed without submitting an order."}
    except AutonomousExecutionError as exc:raise HTTPException(status_code=409,detail=exc.as_dict()) from None


@router.get("/autonomous-runs")
async def recent_autonomous_runs(claims:dict=Depends(current_claims))->dict:
    runner=AutonomousDecisionRunner()
    return {"runs":[runner._public_run(item) for item in runner.execution.recent_decision_runs(claims["sub"],20)]}


@router.get("/activity")
async def activity(account_alias:str|None=None,profile_id:str|None=None,event_type:str|None=None,
                   outcome:str|None=None,date_from:date|None=None,date_to:date|None=None,
                   claims:dict=Depends(current_claims))->dict:
    execution=ExecutionRepository();repo=repository();profiles={item["public_id"]:item for item in repo.list_profiles(claims["sub"])}
    events=[]
    for record in execution.recent_decision_runs(claims["sub"],200):
        public=AutonomousDecisionRunner._public_run(record);profile=profiles.get(str(record.get("profile_ref")),{})
        events.append({"id":record.get("id"),"event_type":"strategy_evaluated","occurred_at":record.get("completed_at") or record.get("started_at"),
            "account_alias":profile.get("account_alias"),"account_number":profile.get("account_number"),
            "environment":profile.get("account_environment"),"profile_id":record.get("profile_ref"),"strategy_name":profile.get("name"),
            "outcome":public["outcome"],"symbol":public.get("symbol"),"reason_codes":public.get("reason_codes",[]),
            "confidence":(record.get("decision") or {}).get("confidence"),"dry_run":bool(record.get("shadow_mode")),"run_id":record.get("id")})
    for record in execution.recent_executions(claims["sub"],100):
        events.append({"id":record.get("id"),"event_type":"demo_order_submitted","occurred_at":record.get("created_at"),
            "profile_id":record.get("profile_ref"),"outcome":str(record.get("state") or "submitted").upper(),
            "symbol":record.get("symbol"),"environment":"demo"})
    for record in execution.autonomous_control_audit(claims["sub"],100):
        enabled=bool(record.get("new_value"));control=record.get("control_name")
        kind=("kill_switch_enabled" if enabled else "kill_switch_disabled") if control=="global_autonomous_kill_switch" else "safety_control_updated"
        events.append({"id":f"control-{record.get('id')}","event_type":kind,"occurred_at":record.get("changed_at"),
            "outcome":"CONFIRMED","reason_codes":[record.get("reason")] if record.get("reason") else []})
    def keep(item:dict[str,Any])->bool:
        stamp=str(item.get("occurred_at") or "")[:10]
        return (not account_alias or item.get("account_alias")==account_alias) and (not profile_id or item.get("profile_id")==profile_id) \
            and (not event_type or item.get("event_type")==event_type) and (not outcome or item.get("outcome")==outcome) \
            and (not date_from or stamp>=date_from.isoformat()) and (not date_to or stamp<=date_to.isoformat())
    filtered=sorted((item for item in events if keep(item)),key=lambda item:str(item.get("occurred_at") or ""),reverse=True)
    return {"events":filtered,"count":len(filtered),"generated_at":datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def user_status(claims:dict=Depends(current_claims))->dict:
    repo=repository();connections=repo.list_connections(claims["sub"]);accounts=repo.list_accounts(claims["sub"])
    worker=ScheduleRepository().worker_health();configured_finnhub=bool(settings.finnhub_enabled and settings.finnhub_api_key)
    configured_fred=bool(settings.fred_enabled and settings.fred_api_key)
    verified=[str(item.get("last_verified_at")) for item in accounts if item.get("last_verified_at")]
    return {"services":[
        {"id":"application_api","label":"Application API","status":"available","message":"The dashboard can reach its backend."},
        {"id":"broker","label":"Trading connection","status":"configured" if any(item.get("enabled") for item in connections) else "needs_attention",
         "message":"Saved connections are managed in Connections. Order review verifies the broker account again."},
        {"id":"market_data","label":"Kraken prices and charts","status":"configured",
         "message":"Public exchange feed. Markets shows the current stream state and received trades."},
        {"id":"news","label":"Research news and calendar","status":"configured",
         "message":"Official ECB, BLS, and BEA feeds are enabled. Availability and coverage can vary by source."},
        {"id":"news_blackout","label":"Trading news validation","status":"configured" if configured_finnhub else "not_configured",
         "message":"Separate from research headlines. Strategies that require news-blackout validation remain blocked without it."},
        {"id":"macro","label":"FRED macro data","status":"configured" if configured_fred else "not_configured",
         "message":"Optional macro provider. Strategies requiring it fail closed when it is unavailable."},
        {"id":"automation","label":"Scheduled automation","status":"available" if worker["status"]=="healthy" else "not_running",
         "message":"Worker heartbeat is current." if worker["status"]=="healthy" else "No healthy worker is connected. Strategies and schedules can still be configured."}],
        "last_successful_sync":max(verified) if verified else None,"generated_at":datetime.now(timezone.utc).isoformat()}



@router.get("/autonomous-schedules")
async def list_autonomous_schedules_api(claims:dict=Depends(current_claims))->dict:
    return {"schedules":AutonomousScheduleService().list(claims["sub"])}


@router.post("/execution-profiles/{profile_id}/autonomy/schedule")
async def save_autonomous_schedule(profile_id:str,payload:AutonomousScheduleRequest,claims:dict=Depends(current_claims))->dict:
    try:return AutonomousScheduleService().save(claims["sub"],profile_id,timezone_name=payload.timezone,
        local_times=payload.local_times,recurrence=payload.recurrence,enabled=payload.enabled,
        maximum_lateness_seconds=payload.maximum_lateness_seconds)
    except ScheduleStorageError as exc:raise HTTPException(status_code=409,detail=str(exc)) from None


@router.put("/autonomous-schedules/{schedule_id}")
async def edit_autonomous_schedule(schedule_id:str,payload:AutonomousScheduleRequest,claims:dict=Depends(current_claims))->dict:
    repo=ScheduleRepository();existing=repo.get_schedule(claims["sub"],schedule_id)
    if not existing:raise HTTPException(status_code=404,detail="Schedule not found.")
    try:return AutonomousScheduleService(schedules=repo).save(claims["sub"],existing["profile_ref"],timezone_name=payload.timezone,
        local_times=payload.local_times,recurrence=payload.recurrence,enabled=payload.enabled,
        maximum_lateness_seconds=payload.maximum_lateness_seconds)
    except ScheduleStorageError as exc:raise HTTPException(status_code=409,detail=str(exc)) from None


@router.post("/autonomous-schedules/{schedule_id}/{action}")
async def control_autonomous_schedule(schedule_id:str,action:Literal["pause","resume"],claims:dict=Depends(current_claims))->dict:
    try:return AutonomousScheduleService().set_enabled(claims["sub"],schedule_id,action=="resume")
    except ScheduleStorageError as exc:raise HTTPException(status_code=404,detail=str(exc)) from None


@router.delete("/autonomous-schedules/{schedule_id}")
async def delete_autonomous_schedule(schedule_id:str,claims:dict=Depends(current_claims))->dict:
    if not ScheduleRepository().delete_schedule(claims["sub"],schedule_id):raise HTTPException(status_code=404,detail="Schedule not found.")
    return {"status":"deleted","schedule_id":schedule_id}


@router.get("/autonomous-schedules/{schedule_id}")
async def autonomous_schedule_status_api(schedule_id:str,claims:dict=Depends(current_claims))->dict:
    try:return AutonomousScheduleService().status(claims["sub"],schedule_id)
    except ScheduleStorageError as exc:raise HTTPException(status_code=404,detail=str(exc)) from None


@router.post("/autonomous-schedule-runs/{dispatch_id}/retry")
async def retry_autonomous_schedule_run(dispatch_id:str,claims:dict=Depends(current_claims))->dict:
    when=(datetime.now(timezone.utc)+timedelta(seconds=settings.autonomous_scheduler_retry_base_seconds)).isoformat()
    if not ScheduleRepository().request_safe_retry(claims["sub"],dispatch_id,when):
        raise HTTPException(status_code=409,detail="Only a failed pre-submit run categorized as safe can be retried.")
    return {"status":"retry_scheduled","dispatch_id":dispatch_id,"next_retry_at":when}


@router.get("/autonomous-daily-summary")
async def autonomous_daily_summary_api(day:date|None=None,claims:dict=Depends(current_claims))->dict:
    return AutonomousScheduleService().daily_summary(claims["sub"],day)


@router.get("/autonomous-worker-health")
async def autonomous_worker_health_api(claims:dict=Depends(current_claims))->dict:
    return ScheduleRepository().worker_health()


@router.post("/operations/kill-switch/enable")
async def enable_kill_switch_api(claims:dict=Depends(current_claims))->dict:
    ExecutionRepository().enable_kill_switch(claims["sub"],source="dashboard",reason="Legacy enable endpoint")
    logger.warning("kill_switch_enabled user_id=%s source=dashboard",claims["sub"])
    return {"status":"enabled","kill_switch":True}


@router.get("/execution-profiles/{profile_id}/demo-status")
async def demo_profile_status(profile_id:str,claims:dict=Depends(current_claims))->dict:
    try:return await AutonomousDemoService().status(claims["sub"],profile_id)
    except AutonomousExecutionError as exc:raise HTTPException(status_code=409,detail=exc.as_dict()) from None


@router.get("/demo-executions")
async def recent_demo_executions(claims:dict=Depends(current_claims))->dict:
    return {"executions":ExecutionRepository().recent_executions(claims["sub"],20)}


@router.get("/broker/tradelocker/execution-settings")
async def get_execution_settings(claims: dict = Depends(current_claims)) -> dict:
    return {"profiles": repository().list_profiles(claims["sub"])}


@router.put("/broker/tradelocker/execution-settings")
async def update_execution_settings(
    payload: ExecutionSettingsUpdate, claims: dict = Depends(current_claims)
) -> dict:
    if payload.execution_mode == ExecutionMode.DEMO_AUTONOMOUS:
        raise HTTPException(status_code=409, detail="Demo Autonomous is not implemented.")
    changed = repository().update_profile(claims["sub"], payload.profile_ref, execution_mode=payload.execution_mode.value)
    if not changed:
        raise HTTPException(status_code=404, detail="Execution profile not found.")
    logger.info(
        "TradeLocker execution_mode_changed user_id=%s profile_ref=%s mode=%s",
        claims["sub"], payload.profile_ref, payload.execution_mode.value,
    )
    return {"status": "updated", "execution_mode": payload.execution_mode.value, "profile_ref": payload.profile_ref}
