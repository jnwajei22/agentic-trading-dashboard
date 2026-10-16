from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any, Literal

from pydantic import ValidationError

from app.auth.identity import get_current_user_sub
from app.brokers.tradelocker.adapter import TradeLockerAdapter, get_tradelocker_adapter
from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.brokers.tradelocker.mapping import TradeLockerMappingError, map_configured_rows
from app.brokers.paper.adapter import PaperBrokerAdapter
from app.config.settings import settings
from app.models.orders import OrderRequest
from app.models.autonomous import (
    AutonomousNoTradeRequest,
    AutonomousOrderProposal,
    AutonomousSubmissionRequest,
)
from app.models.tradelocker import (
    TradeLockerAccountStatus,
    TradeLockerAccountStatusError,
)
from app.models.chart_widget import RenderMarketChartRequest
from app.services.market_data.librarian import (
    get_macro_results,
    get_market_series,
    macro_catalog,
    watchlist_market_data,
)
from app.services.providers.errors import ProviderError
from app.services.providers.finnhub import FinnhubClient, capability_status
from app.services.providers.fred import FredClient
from app.services.market_data.series_cache import market_series_cache
from app.services.trading.previews import create_order_preview
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError, normalize_pair
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.services.autonomous.control import AutonomyControlService, AutonomousControlError
from app.jobs.autonomous_scheduler import AutonomousScheduleService
from app.storage.schedules import ScheduleStorageError
from app.storage.execution import ExecutionRepository
from app.services.tradelocker.account_status import (
    AccountStatusUnavailable,
    TradeLockerAccountStatusService,
)
from app.services.tradelocker.accounts import AccountResolutionError, BrokerAccountResolver
from app.services.watchlist import get_default_watchlist
from app.storage.brokers import BrokerRepository, BrokerStorageError


logger = logging.getLogger(__name__)

DEMO_BLOCKING_MESSAGES={
    "pair_not_allowed":"The symbol is not allowed by the bound execution profile.",
    "duplicate_setup":"An active preview already exists for this symbol and account.",
    "invalid_protective_prices":"Entry, stop loss, and take profit are inconsistent with the order side.",
    "reward_risk_too_low":"The server-calculated reward-to-risk ratio is below the profile minimum.",
    "provider_unavailable":"Required Finnhub market-event data is unavailable or stale.",
    "required_macro_provider_unavailable":"Strategy-required FRED macro data is unavailable or stale.",
    "news_blackout":"A configured economic-news blackout is active.",
    "position_or_order_limit":"The profile-bound account has reached its open-position or pending-order limit.",
    "market_data_incomplete":"The TradeLocker market snapshot is incomplete.",
    "broker_stop_distance_invalid":"The stop distance is below the broker minimum.",
    "position_size_unverifiable":"Broker quantity, conversion, or instrument metadata could not be verified.",
    "margin_unverifiable":"Broker margin requirements could not be verified.",
    "insufficient_margin":"The server-calculated order margin exceeds available funds.",
    "spread_too_wide":"The TradeLocker spread exceeds the configured maximum.",
    "profile_account_context_mismatch":"The profile-bound account context changed during validation.",
    "account_mapping_unavailable":"The profile-bound account state could not be mapped safely.",
    "market_snapshot_unavailable":"TradeLocker positions, orders, quote, candles, or configuration are unavailable.",
}


def _setup_url() -> str:
    return f"{settings.frontend_origin.rstrip('/')}/connect-tradelocker"


def _display_broker(value: str | None) -> str:
    normalized=(value or "TradeLocker").strip().lower()
    return {"herofx":"HeroFX","tradelocker":"TradeLocker"}.get(normalized, (value or "TradeLocker").strip())


def _display_value(value: str | None) -> str:
    labels={"demo":"Demo","live":"Live","unknown":"Unknown","read_only":"Read Only",
        "demo_manual":"Demo Manual","demo_autonomous":"Demo Autonomous","disabled":"Disabled"}
    return labels.get((value or "unknown").lower(), (value or "Unknown").replace("_"," ").title())


def _safe_account_output(item: dict[str,Any]) -> dict[str,Any]:
    classification="Demo" if item.get("is_demo") == 1 else "Live" if item.get("is_demo") == 0 else "Unknown"
    return {
        "account_ref":item["public_id"], "alias":item["account_alias"], "name":item.get("account_name"),
        "broker":_display_broker(item.get("broker_name") or item.get("server")),
        "connection_label":item.get("connection_label"), "environment":_display_value(item.get("environment")),
        "classification":classification, "currency":item.get("currency"),
        "availability":"Active" if item.get("available") and item.get("locally_enabled") else "Unavailable",
        "is_default":bool(item.get("is_default_analysis")),
        "profiles":[{"profile_ref":p["public_id"],"name":p["name"],
            "operating_state":"Manual","enabled":p["enabled"],"execution_mode_deprecated":True} for p in item.get("profiles",[])],
    }


def _setup_required() -> dict[str, Any]:
    return {
        "status": "setup_required", "message": "TradeLocker setup required.",
        "setup_url": _setup_url(),
        "instruction": (
            "Open the setup URL, connect TradeLocker using the same login account, "
            "then retry in the desk assistant."
        ),
    }


def _missing_user_connection() -> dict[str, Any] | None:
    user_sub = get_current_user_sub()
    if not user_sub:
        return None
    try:
        status = BrokerRepository().status(user_sub)
    except BrokerStorageError as exc:
        return {"status": "error", "error": "broker_storage_error", "message": str(exc)}
    return _setup_required() if status["status"] == "not_connected" else None


def _tradelocker_error(exc: TradeLockerError) -> dict[str, Any]:
    if exc.code == "setup_required" or exc.status_code in {401, 403}:
        return _setup_required()
    return exc.as_dict()


def _date(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must use YYYY-MM-DD format.") from None


def get_tradelocker_connection_status() -> dict[str, Any]:
    """Return the current user's sanitized, isolated TradeLocker connection status."""
    user_sub = get_current_user_sub()
    if not user_sub:
        return {"connected": False, "selected_account": False, **_setup_required()}
    try:
        status = BrokerRepository().status(user_sub)
    except BrokerStorageError as exc:
        return {"connected": False, "selected_account": False, "status": "error", "message": str(exc)}
    if status["status"] == "not_connected":
        return {"connected": False, "selected_account": False, **_setup_required()}
    selected = status["status"] == "ready"
    result: dict[str, Any] = {"connected": True, "selected_account": selected, "status": status["status"]}
    if selected:
        result["selected_account_summary"] = status["selected_account"]
    return result


def get_my_broker_connection_status() -> dict[str, Any]:
    """Deprecated compatibility alias for get_tradelocker_connection_status."""
    return get_tradelocker_connection_status()


def get_forex_watchlist() -> list[dict[str, Any]]:
    """Return configured forex symbols without analysis or ranking."""
    return [item.model_dump(mode="json") for item in get_default_watchlist()]


async def get_market_candles(
    symbol: str,
    timeframe: str,
    source: Literal["tradelocker", "finnhub"] = "tradelocker",
    lookback: int | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_candles: int | None = None,
    account_alias: str | None = None,
    account_ref: str | None = None,
) -> dict[str, Any]:
    """Return canonical provider-identified OHLCV for client-side charting and analysis; explicit dates override lookback."""
    owner_id = get_current_user_sub()
    if source == "tradelocker":
        missing = _missing_user_connection()
        if missing:
            return missing
    try:
        adapter = None
        if source == "tradelocker" and owner_id:
            context = BrokerAccountResolver().resolve(owner_id, account_alias=account_alias, account_ref=account_ref)
            adapter = TradeLockerAdapter(TradeLockerClient(base_url=context.base_url,username=context.username,
                password=context.password,server=context.server,account_id=context.account_id,account_number=context.account_number,
                cache_user_id=owner_id,cache_connection_id=getattr(context,"connection_ref",None),
                cache_account_record_id=getattr(context,"account_record_id",None)))
        result = await get_market_series(
            symbol=symbol, timeframe=timeframe, source=source, lookback=lookback,
            start_time=start_time, end_time=end_time, max_candles=max_candles,
            tradelocker_adapter=adapter,
        )
        if owner_id:
            cached = market_series_cache.put(owner_id, result)
            return cached.series.model_dump(mode="json")
        return result.model_dump(mode="json")
    except ProviderError as exc:
        return exc.as_dict()
    except TradeLockerError as exc:
        return _tradelocker_error(exc)
    except AccountResolutionError as exc:
        return _setup_required() if exc.code == "default_account_required" else {"status":"error","error":exc.code,"message":str(exc)}
    except ValueError as exc:
        return ProviderError(source, "invalid_request", str(exc)).as_dict()


def _chart_error(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    structured: dict[str, Any] = {"status": "error", "error": code, "message": message}
    if details:
        structured["details"] = details
    return structured



def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors(include_url=False, include_input=False)
    ]


def _timestamps_are_near_series(request: RenderMarketChartRequest, start: Any, end: Any) -> bool:
    if start is None or end is None:
        return not request.line_overlays and not request.markers
    padding = max(timedelta(days=7), (end - start) / 10)
    minimum, maximum = start - padding, end + padding
    timestamps = [point.timestamp for overlay in request.line_overlays for point in overlay.points]
    timestamps.extend(marker.timestamp for marker in request.markers)
    return all(minimum <= timestamp <= maximum for timestamp in timestamps)


async def render_market_chart(
    series_id: str,
    chart_type: Literal["candlestick", "line"] = "candlestick",
    title: str | None = None,
    show_volume: bool = True,
    horizontal_overlays: list[dict[str, Any]] | None = None,
    line_overlays: list[dict[str, Any]] | None = None,
    markers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Use this tool whenever the user asks to see, show, display, draw, plot, or render a forex chart. Call get_market_candles first, then pass its series_id here. This tool renders an inline interactive chart."""
    owner_id = get_current_user_sub()
    if not owner_id:
        return _chart_error("series_access_denied", "An authenticated user is required.")
    status, series = market_series_cache.get(owner_id, series_id)
    if status != "found" or series is None:
        messages = {
            "expired": ("series_expired", "The market series expired; call get_market_candles again."),
            "access_denied": ("series_access_denied", "This market series is not available to the current user."),
            "not_found": ("series_not_found", "The market series was not found; call get_market_candles first."),
        }
        code, message = messages[status]
        return _chart_error(code, message)
    try:
        request = RenderMarketChartRequest.model_validate({
            "series_id": series_id,
            "chart_type": chart_type,
            "title": title,
            "show_volume": show_volume,
            "horizontal_overlays": horizontal_overlays or [],
            "line_overlays": line_overlays or [],
            "markers": markers or [],
        })
    except ValidationError as exc:
        details = _validation_details(exc)
        code = "invalid_marker" if any(item["field"].startswith("markers") for item in details) else "invalid_overlay"
        return _chart_error(code, "Chart annotations failed validation.", details)
    if not series.candles:
        return _chart_error("chart_payload_empty", "The cached market series contains no candles.")
    if not _timestamps_are_near_series(request, series.actual_start, series.actual_end):
        return _chart_error(
            "invalid_marker" if request.markers and not request.line_overlays else "invalid_overlay",
            "Annotation timestamps are too far outside the cached market range.",
        )

    chart = {
        "title": request.title,
        "symbol": series.normalized_symbol,
        "timeframe": series.timeframe,
        "source": series.source,
        "actual_start": series.actual_start.isoformat().replace("+00:00", "Z") if series.actual_start else None,
        "actual_end": series.actual_end.isoformat().replace("+00:00", "Z") if series.actual_end else None,
        "complete": series.complete,
        "warning": series.warning,
        "chart_type": request.chart_type,
        "show_volume": request.show_volume,
        "candles": [candle.model_dump(mode="json") for candle in series.candles],
        "horizontal_overlays": [item.model_dump(mode="json") for item in request.horizontal_overlays],
        "line_overlays": [item.model_dump(mode="json") for item in request.line_overlays],
        "markers": [item.model_dump(mode="json") for item in request.markers],
    }
    summary = {
        "status": "ready", "series_id": series_id,
        "symbol": series.normalized_symbol, "timeframe": series.timeframe,
        "source": series.source, "chart_type": request.chart_type,
        "candles_rendered": len(series.candles),
        "horizontal_overlays": len(request.horizontal_overlays),
        "line_overlays": len(request.line_overlays), "markers": len(request.markers),
        "complete": series.complete,
    }
    return {**summary, "chart": chart}



async def get_watchlist_market_data(
    symbols: list[str], timeframe: str, lookback: int = 100,
    fields: list[str] | None = None, max_symbols: int = 10,
) -> dict[str, Any]:
    """Return bounded TradeLocker series for client-side screening without ranking or recommendations."""
    missing = _missing_user_connection()
    if missing:
        return missing
    return await watchlist_market_data(symbols, timeframe, lookback, fields, max_symbols)


async def get_economic_calendar(
    start_date: str, end_date: str, countries: list[str] | None = None,
    currencies: list[str] | None = None, limit: int = 100,
) -> dict[str, Any]:
    """Return normalized Finnhub economic events when the configured plan permits access."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    client = FinnhubClient()
    try:
        events = await client.economic_calendar(_date(start_date, "start_date"), _date(end_date, "end_date"), limit)
        country_set = {value.upper() for value in countries or []}
        currency_set = {value.upper() for value in currencies or []}
        events = [event for event in events if (not country_set or (event.country or "").upper() in country_set) and (not currency_set or (event.currency or "").upper() in currency_set)]
        return {"source": "finnhub", "capability": "economic_calendar", "events": [event.model_dump(mode="json") for event in events[:limit]]}
    except ProviderError as exc:
        return exc.as_dict()
    finally:
        await client.aclose()


async def get_market_news(
    symbols: list[str] | None = None, currencies: list[str] | None = None,
    category: str = "forex", start_date: str | None = None,
    end_date: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """Return bounded Finnhub headlines and concise summaries, never full articles."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date")
    client = FinnhubClient()
    try:
        items = await client.market_news(category, limit=100)
        wanted = {value.upper() for value in (symbols or []) + (currencies or [])}
        filtered = []
        for item in items:
            published = item.published_at.date()
            if start and published < start or end and published > end:
                continue
            searchable = {value.upper() for value in item.related_symbols}
            searchable.update(item.headline.upper().split())
            if wanted and not any(value in searchable or value in item.headline.upper() for value in wanted):
                continue
            filtered.append(item)
        return {"source": "finnhub", "capability": "market_news", "items": [item.model_dump(mode="json") for item in filtered[:limit]]}
    except ProviderError as exc:
        return exc.as_dict()
    finally:
        await client.aclose()


async def search_macro_series(query: str, limit: int = 25) -> dict[str, Any]:
    """Search official FRED series metadata; no series identifiers are fabricated."""
    client = FredClient()
    try:
        results = await client.search_series(query, limit)
        return {"source": "fred", "results": [item.model_dump(mode="json") for item in results]}
    except ProviderError as exc:
        return exc.as_dict()
    finally:
        await client.aclose()


async def get_macro_series(
    series_ids: list[str], observation_start: str | None = None,
    observation_end: str | None = None, realtime_start: str | None = None,
    realtime_end: str | None = None, limit: int = 1000,
) -> dict[str, Any]:
    """Return official FRED metadata and observations with real-time periods preserved."""
    if not 1 <= limit <= 5000 or not 1 <= len(series_ids) <= 10:
        raise ValueError("Request up to 10 series and 1 to 5000 observations per series.")
    try:
        results = await get_macro_results(
            series_ids, _date(observation_start, "observation_start"),
            _date(observation_end, "observation_end"), _date(realtime_start, "realtime_start"),
            _date(realtime_end, "realtime_end"), limit,
        )
        return {"source": "fred", "series": [item.model_dump(mode="json") for item in results]}
    except ProviderError as exc:
        return exc.as_dict()


async def get_macro_release_calendar(
    start_date: str | None = None, end_date: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """Return official FRED release dates."""
    client = FredClient()
    try:
        dates = await client.release_dates(_date(start_date, "start_date"), _date(end_date, "end_date"), limit)
        return {"source": "fred", "release_dates": [item.model_dump(mode="json") for item in dates]}
    except ProviderError as exc:
        return exc.as_dict()
    finally:
        await client.aclose()


async def get_forex_research_bundle(
    symbol: str, timeframe: str, lookback: int | None = None,
    start_time: str | None = None, end_time: str | None = None,
    include_quote: bool = True, include_account_exposure: bool = True,
    include_calendar: bool = True, include_news: bool = True, include_macro: bool = True,
    news_limit: int = 10, event_limit: int = 20,
    macro_series_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Retrieve bounded, separate forex research inputs without analysis, prediction, or recommendations."""
    market = await get_market_candles(symbol, timeframe, "tradelocker", lookback, start_time, end_time, settings.market_data_max_response_candles)
    if market.get("status") in {"error", "setup_required"}:
        return market
    warnings: list[str] = []
    quote_data: Any = None
    exposure: Any = None
    if include_quote:
        quote_data = await get_tradelocker_quote(symbol)
    if include_account_exposure:
        exposure = await get_open_positions()
    calendar: Any = None
    news: Any = None
    macro: Any = None
    today = date.today().isoformat()
    if include_calendar:
        calendar = await get_economic_calendar(today, today, limit=event_limit)
        if calendar.get("status") == "error":
            warnings.append(calendar["message"])
    if include_news:
        news = await get_market_news(symbols=[symbol], limit=news_limit)
        if news.get("status") == "error":
            warnings.append(news["message"])
    if include_macro:
        ids = macro_series_ids or macro_catalog().currencies.get(symbol[:3].upper(), []) + macro_catalog().currencies.get(symbol[-3:].upper(), [])
        macro = await get_macro_series(list(dict.fromkeys(ids))[:10], limit=500) if ids else {"source": "fred", "series": [], "warning": "No macro series IDs were requested or configured."}
        if macro.get("status") == "error":
            warnings.append(macro["message"])
    return {
        "symbol": symbol, "market": market, "quote": quote_data,
        "account_exposure": exposure, "economic_calendar": calendar,
        "news": news, "macro": macro, "warnings": warnings,
        "sources": {"execution": "tradelocker", "calendar": "finnhub", "news": "finnhub", "macro": "fred"},
    }


async def get_account_status(account_alias: str | None = None) -> dict[str, Any]:
    """Return normalized, labeled state for the authenticated user's selected TradeLocker account. This read-only tool never returns paper data or an unlabeled broker array, and a zero balance is valid."""
    user_sub = get_current_user_sub()
    if not user_sub:
        return TradeLockerAccountStatusError(
            error="authentication_required",
            message="Authenticate with Agentic Forex Desk before requesting TradeLocker account status.",
        ).model_dump(mode="json")
    try:
        service = TradeLockerAccountStatusService()
        result = await (service.retrieve(user_sub) if account_alias is None else service.retrieve(user_sub, account_alias))
        return result.model_dump(mode="json")
    except AccountStatusUnavailable as exc:
        return TradeLockerAccountStatusError(
            error=exc.code,
            message=str(exc),
            setup_url=_setup_url() if exc.code == "setup_required" else None,
        ).model_dump(mode="json")
    except TradeLockerError as exc:
        if exc.operation == "get_config":
            return TradeLockerAccountStatusError(
                error="account_field_mapping_unavailable",
                message=(
                    "TradeLocker account values could not be labeled because their field "
                    "configuration is unavailable."
                ),
            ).model_dump(mode="json")
        if exc.operation == "get_account_state":
            return TradeLockerAccountStatusError(
                error="account_state_unavailable",
                message="TradeLocker account state is temporarily unavailable.",
            ).model_dump(mode="json")
        return TradeLockerAccountStatusError(
            error="tradelocker_authentication_unavailable",
            message="TradeLocker authentication or account verification failed.",
        ).model_dump(mode="json")


async def get_paper_account_status() -> dict[str, Any]:
    """Return the isolated internal paper-account status; never return TradeLocker data."""
    result = await PaperBrokerAdapter().get_account()
    return {"schema_version": "1.0", "status": "ok", "source": "paper", **result}


async def _account_rows(account_alias: str | None, *, orders: bool) -> dict[str, Any]:
    user_sub = get_current_user_sub()
    if not user_sub:
        return {"status": "error", "error": "authentication_required"}
    operation = "get_pending_orders" if orders else "get_open_positions"
    context = None
    try:
        context = BrokerAccountResolver().resolve(user_sub, account_alias=account_alias)
        async with TradeLockerClient(base_url=context.base_url, username=context.username,
            password=context.password, server=context.server, account_id=context.account_id,
            account_number=context.account_number) as client:
            config = await client.get_config()
            data = await (client.get_orders() if orders else client.get_open_positions())
        rows = map_configured_rows(config_response=config, data_response=data,
            config_key="ordersConfig" if orders else "positionsConfig",
            data_key="orders" if orders else "positions")
        logger.info("tradelocker_rows_read operation=%s user_ref=%s account_ref=%s account_alias=%s row_count=%s",
            operation,user_sub,context.account_record_id,context.account_alias,len(rows))
        return {"status": "ok", "account": context.safe_identity(),
                "orders" if orders else "positions": rows}
    except AccountResolutionError as exc:
        logger.warning("tradelocker_rows_failed operation=%s user_ref=%s account_ref=%s error_category=%s",
            operation,user_sub,context.account_record_id if context else None,exc.code)
        return {"status": "error", "error": exc.code, "message": str(exc)}
    except TradeLockerMappingError:
        logger.warning("tradelocker_rows_failed operation=%s user_ref=%s account_ref=%s error_category=account_field_mapping_unavailable",
            operation,user_sub,context.account_record_id if context else None)
        return {"status": "error", "error": "account_field_mapping_unavailable",
                "message": "TradeLocker rows could not be safely labeled."}
    except TradeLockerError as exc:
        logger.warning("tradelocker_rows_failed operation=%s user_ref=%s account_ref=%s error_category=%s",
            operation,user_sub,context.account_record_id if context else None,exc.code)
        return _tradelocker_error(exc)
    except Exception:
        logger.error("tradelocker_rows_failed operation=%s user_ref=%s account_ref=%s error_category=internal_read_error",
            operation,user_sub,context.account_record_id if context else None)
        return {"status":"error","error":"internal_read_error",
            "message":"The TradeLocker account rows could not be retrieved safely."}


async def get_open_positions(account_alias: str | None = None) -> dict[str, Any]:
    """Return current TradeLocker positions without shared caching."""
    return await _account_rows(account_alias, orders=False)


async def get_pending_orders(account_alias: str | None = None) -> dict[str, Any]:
    """Return current TradeLocker pending orders when the endpoint is available."""
    return await _account_rows(account_alias, orders=True)


async def get_trade_history(limit: int = 100) -> dict[str, Any]:
    """Report TradeLocker trade-history capability without inventing an undocumented route."""
    missing = _missing_user_connection()
    if missing:
        return missing
    return {
        "status": "error", "provider": "tradelocker",
        "error": "capability_unavailable",
        "message": "Trade history is not exposed by the currently verified TradeLocker client routes.",
        "capability": "trade_history", "retryable": False,
        "requested_limit": limit,
    }


async def get_tradelocker_config() -> dict[str, Any] | list[Any]:
    missing = _missing_user_connection()
    if missing:
        return missing
    try:
        return await get_tradelocker_adapter().client.get_config()
    except TradeLockerError as exc:
        return _tradelocker_error(exc)


async def get_tradelocker_accounts() -> dict[str, Any]:
    """Discover sanitized TradeLocker accounts before account selection."""
    missing = _missing_user_connection()
    if missing:
        return missing
    try:
        return await get_tradelocker_adapter().client.get_accounts()
    except TradeLockerError as exc:
        return _tradelocker_error(exc)


async def get_tradelocker_symbols(account_alias: str | None = None, account_ref: str | None = None) -> dict[str, Any] | list[Any]:
    """Return symbols for the connected TradeLocker account."""
    missing = _missing_user_connection()
    if missing:
        return missing
    try:
        user=get_current_user_sub()
        context=BrokerAccountResolver().resolve(user,account_alias=account_alias,account_ref=account_ref) if user else None
        if not context:return _setup_required()
        async with TradeLockerClient(base_url=context.base_url,username=context.username,password=context.password,server=context.server,account_id=context.account_id,account_number=context.account_number) as client:
            return await client.get_symbols()
    except AccountResolutionError as exc:
        return _setup_required() if exc.code == "default_account_required" else {"status":"error","error":exc.code,"message":str(exc)}
    except TradeLockerError as exc:
        return _tradelocker_error(exc)


async def get_tradelocker_quote(symbol: str, account_alias: str | None = None, account_ref: str | None = None) -> dict[str, Any] | list[Any]:
    """Return the authoritative TradeLocker execution quote for a symbol."""
    missing = _missing_user_connection()
    if missing:
        return missing
    try:
        user=get_current_user_sub()
        context=BrokerAccountResolver().resolve(user,account_alias=account_alias,account_ref=account_ref) if user else None
        if not context:return _setup_required()
        async with TradeLockerClient(base_url=context.base_url,username=context.username,password=context.password,server=context.server,account_id=context.account_id,account_number=context.account_number) as client:
            return await client.get_quote(symbol)
    except AccountResolutionError as exc:
        return {"status":"error","error":exc.code,"message":str(exc)}
    except TradeLockerError as exc:
        return _tradelocker_error(exc)


def review_forex_order(order_request: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic risk-reviewed preview; never submit an order."""
    return create_order_preview(OrderRequest(**order_request)).model_dump(mode="json")


def set_kill_switch(enabled: bool, reason: str) -> dict[str, Any]:
    """Enable the kill switch; remote callers cannot disable it."""
    if not reason.strip():
        raise ValueError("A reason is required to change the kill switch.")
    if not enabled:
        ExecutionRepository().enable_kill_switch(_authenticated_user(),source="assistant",reason=reason)
        return {"changed": False, "kill_switch_enabled": True, "reason": reason, "message": "Remote MCP callers cannot disable the kill switch."}
    repository=ExecutionRepository();user=_authenticated_user()
    changed=not repository.get_autonomous_controls(user)["global_autonomous_kill_switch"];repository.enable_kill_switch(user,source="assistant",reason=reason)
    return {"changed": changed, "kill_switch_enabled": True, "reason": reason, "message": "Kill switch enabled."}


def _authenticated_user() -> str:
    user_sub = get_current_user_sub()
    if not user_sub:
        raise AutonomousExecutionError(
            "no_authenticated_user", "Authenticate before using autonomous-demo tools."
        )
    return user_sub


async def start_autonomous_trading(
    environment: Literal["demo", "live"], account_alias: str | None = None,
    profile_ref: str | None = None, run_now: bool = True,
    use_strategy_schedule: bool = True, live_confirmation: str | None = None,
) -> dict[str, Any]:
    """Start profile-managed autonomy using the owned account, server decision defaults, durable controls, schedule, and runner."""
    try:
        return await AutonomyControlService().start(_authenticated_user(), environment,
            account_alias=account_alias, profile_ref=profile_ref, run_now=run_now,
            use_strategy_schedule=use_strategy_schedule, live_confirmation=live_confirmation)
    except AutonomousControlError as exc:
        return exc.as_dict()
    except (BrokerStorageError, ScheduleStorageError, ValueError):
        logger.exception("start_autonomous_trading_failed error_category=control_persistence_failed")
        return {"status":"blocked","error":"control_persistence_failed",
            "message":"Autonomous control state could not be persisted.",
            "blocking_reasons":["control_persistence_failed"]}


def stop_autonomous_trading(
    environment: Literal["demo", "live"], account_alias: str | None = None,
) -> dict[str, Any]:
    """Stop new autonomous cycles for one environment without changing broker orders, positions, profiles, or schedules."""
    try:
        return AutonomyControlService().stop(_authenticated_user(), environment, account_alias=account_alias)
    except AutonomousControlError as exc:
        return exc.as_dict()
    except (BrokerStorageError, ScheduleStorageError, ValueError):
        return {"status":"blocked","error":"control_persistence_failed",
            "message":"Autonomous control state could not be persisted.",
            "blocking_reasons":["control_persistence_failed"]}


def emergency_stop_autonomous_trading(reason: str) -> dict[str, Any]:
    """Enable the global autonomous kill switch while preserving all existing broker orders and positions."""
    try:
        return AutonomyControlService().emergency_stop(_authenticated_user(), reason)
    except AutonomousControlError as exc:
        return exc.as_dict()
    except (BrokerStorageError, ValueError):
        return {"status":"blocked","error":"control_persistence_failed",
            "message":"The emergency stop could not be persisted.",
            "blocking_reasons":["control_persistence_failed"]}


def get_autonomous_trading_status() -> dict[str, Any]:
    """Return concise owned controls, accounts, profiles, provider readiness, worker health, schedules, runs, and blockers."""
    try:
        return AutonomyControlService().status(_authenticated_user())
    except AutonomousControlError as exc:
        return exc.as_dict()
    except (BrokerStorageError, ScheduleStorageError, ValueError):
        return {"status":"blocked","error":"autonomous_status_unavailable",
            "message":"Autonomous trading status is unavailable.",
            "blocking_reasons":["autonomous_status_unavailable"]}


async def get_autonomous_demo_status(profile_ref: str) -> dict[str, Any]:
    """Read durable autonomous-control and blocker state for one owned profile."""
    try:
        return await AutonomousDecisionRunner().status(_authenticated_user(), profile_ref)
    except AutonomousExecutionError as exc:
        return exc.as_dict()


async def get_autonomous_demo_snapshot(profile_ref: str) -> dict[str, Any]:
    """Build a bounded multi-timeframe decision snapshot for one enabled demo profile; never submits."""
    try:
        return await AutonomousDecisionRunner().snapshot(_authenticated_user(), profile_ref)
    except AutonomousExecutionError as exc:
        return exc.as_dict()


async def review_autonomous_demo_order(
    profile_ref: str, snapshot_id: str, pair: str, side: Literal["long", "short"],
    order_type: Literal["market", "limit"], entry: float,
    stop_loss: float, take_profit: float,
    reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Create an immutable server-sized preview for the authenticated user's verified TradeLocker demo account; this does not submit."""
    try:
        proposal = AutonomousOrderProposal(
            snapshot_id=snapshot_id, pair=pair, side=side, order_type=order_type,
            entry=entry, stop_loss=stop_loss, take_profit=take_profit,
            reason_codes=reason_codes or [],
        )
        return await AutonomousDemoService().review(_authenticated_user(), profile_ref, proposal, autonomous=True)
    except ValidationError as exc:
        return {"schema_version": "1.0", "status": "rejected", "error": "invalid_proposal", "message": "The order proposal schema is invalid.", "violations": [item["type"] for item in exc.errors()]}
    except AutonomousExecutionError as exc:
        return exc.as_dict()


async def submit_autonomous_demo_order(preview_id: str, idempotency_key: str) -> dict[str, Any]:
    """Submit one risk-approved order to the authenticated user's verified TradeLocker demo account. This consequential broker-side write cannot target a live account."""
    try:
        request = AutonomousSubmissionRequest(preview_id=preview_id, idempotency_key=idempotency_key)
        service=AutonomousDemoService();preview=service.execution.get_preview(request.preview_id)
        if not preview or preview.get("execution_origin")!="autonomous":
            raise AutonomousExecutionError("autonomous_preview_required","An autonomous-origin preview is required.",status="rejected")
        return await service.submit(_authenticated_user(), request.preview_id, request.idempotency_key)
    except ValidationError:
        return {"schema_version": "1.0", "status": "rejected", "error": "invalid_submission", "message": "Only a valid preview ID and idempotency key are accepted."}
    except AutonomousExecutionError as exc:
        return exc.as_dict()


async def record_autonomous_no_trade(profile_ref: str, snapshot_id: str, reason_codes: list[str], pairs_evaluated: list[str]) -> dict[str, Any]:
    """Persist a deliberate no-trade decision for an owned fresh snapshot; this never contacts TradeLocker order endpoints."""
    try:
        request = AutonomousNoTradeRequest(snapshot_id=snapshot_id, reason_codes=reason_codes, pairs_evaluated=pairs_evaluated)
        return await AutonomousDemoService().record_no_trade(_authenticated_user(), profile_ref, request.snapshot_id, request.reason_codes, request.pairs_evaluated)
    except ValidationError:
        return {"schema_version": "1.0", "status": "rejected", "error": "invalid_no_trade_record", "message": "The no-trade record schema is invalid."}
    except AutonomousExecutionError as exc:
        return exc.as_dict()


async def run_autonomous_demo_profile(profile_ref:str,run_key:str,trigger_reason:str)->dict[str,Any]:
    """Run one idempotent bounded decision cycle under durable environment controls and profile-bound safeguards."""
    try:return await AutonomousDecisionRunner().run(_authenticated_user(),profile_ref,run_key,trigger_reason)
    except AutonomousExecutionError as exc:return exc.as_dict()


def get_autonomous_run_result(run_id: str | None = None) -> dict[str, Any]:
    """Read the latest or requested autonomous-demo audit result for the authenticated user."""
    try:
        if run_id:return AutonomousDecisionRunner().result(_authenticated_user(),run_id)
        latest=AutonomousDecisionRunner().execution.get_decision_run(_authenticated_user())
        return AutonomousDecisionRunner._public_run(latest) if latest else AutonomousDemoService().run_result(_authenticated_user(),None)
    except AutonomousExecutionError as exc:
        return exc.as_dict()


def list_autonomous_schedules()->dict[str,Any]:
    """List owned autonomous schedules and their local/UTC next-run times; this cannot mutate scheduling."""
    return {"status":"ok","schedules":AutonomousScheduleService().list(_authenticated_user())}


def get_autonomous_schedule_status(schedule_id:str)->dict[str,Any]:
    """Read one owned schedule and its recent durable dispatch history."""
    try:return AutonomousScheduleService().status(_authenticated_user(),schedule_id)
    except ScheduleStorageError:return {"status":"not_found","error":"schedule_not_found"}


def list_recent_autonomous_runs(profile_ref:str|None=None,limit:int=20)->dict[str,Any]:
    """List recent owned autonomous decisions without credentials or broker routing identifiers."""
    user=_authenticated_user();runner=AutonomousDecisionRunner()
    records=runner.execution.recent_decision_runs(user,min(max(limit,1),50))
    if profile_ref:records=[item for item in records if item.get("profile_ref")==profile_ref]
    return {"status":"ok","runs":[runner._public_run(item) for item in records]}


def get_autonomous_daily_summary(day:str|None=None)->dict[str,Any]:
    """Return the authenticated user's concise UTC autonomous daily summary."""
    try:target=date.fromisoformat(day) if day else None
    except ValueError:return {"status":"error","error":"invalid_date","message":"day must be YYYY-MM-DD"}
    return AutonomousScheduleService().daily_summary(_authenticated_user(),target)


# Profile-bound demo execution API. These names intentionally expose only safe profile,
# preview, execution, order, and position references; broker account routing is internal.
async def get_demo_execution_status(profile_id: str) -> dict[str,Any]:
    user=_authenticated_user()
    try:
        result=await AutonomousDemoService().status(user,profile_id)
        logger.info("demo_execution_status user_ref=%s profile_ref=%s status=%s blockers=%s",
            user,profile_id,result.get("status"),result.get("blocking_reasons",[]))
        return result
    except AutonomousExecutionError as exc:
        logger.warning("demo_execution_status_failed user_ref=%s profile_ref=%s error_category=%s",
            user,profile_id,exc.code)
        return exc.as_dict()
    except Exception:
        logger.error("demo_execution_status_failed user_ref=%s profile_ref=%s error_category=internal_status_error",
            user,profile_id)
        return {"schema_version":"1.0","status":"blocked","error":"internal_status_error",
            "message":"Demo execution status is temporarily unavailable.",
            "blocking_reasons":["internal_status_error"],"can_submit_demo_orders":False}


async def get_demo_trading_snapshot(profile_id: str,symbol: str)->dict[str,Any]:
    try:return await AutonomousDemoService().snapshot(_authenticated_user(),profile_id,symbol)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def review_demo_order(profile_id:str,symbol:str,side:Literal["long","short"],order_type:Literal["market","limit","stop"],
                            stop_loss:float,take_profit:float,reason:str,entry:float|None=None)->dict[str,Any]:
    user=_authenticated_user()
    try:
        if not reason.strip() or len(reason)>500:raise AutonomousExecutionError("invalid_reason","A concise human-readable reason is required.",status="rejected")
        service=AutonomousDemoService();snapshot=await service.snapshot(user,profile_id,symbol)
        if entry is None:
            if order_type!="market":raise AutonomousExecutionError("entry_required","Limit and stop orders require an entry price.",status="rejected")
            bid,ask=service._quote(snapshot["market"]["pairs"][normalize_pair(symbol)]["quote"]);entry=ask if side=="long" else bid
        proposal=AutonomousOrderProposal(snapshot_id=snapshot["snapshot_id"],pair=symbol,side=side,order_type=order_type,
            entry=entry,stop_loss=stop_loss,take_profit=take_profit,reason_codes=["user_reason"])
        result=await service.review(user,profile_id,proposal)
        required=("preview_id","expires_at","quantity","estimated_risk","estimated_margin")
        if not isinstance(result,dict) or any(result.get(field) is None for field in required):
            logger.error("demo_order_review_failed user_ref=%s profile_ref=%s symbol=%s error_category=preview_payload_missing",
                user,profile_id,normalize_pair(symbol))
            return _demo_review_error("preview_payload_missing","The validated preview payload is incomplete.",
                symbol=symbol,order_type=order_type)
        result["reason"]=reason;result["submission_allowed"]=result.get("status")=="approved"
        result["blocking_reasons"]=result.get("violations") or []
        logger.info("demo_order_reviewed user_ref=%s profile_ref=%s symbol=%s preview_id=%s submission_allowed=%s",
            user,profile_id,normalize_pair(symbol),result["preview_id"],result["submission_allowed"])
        return result
    except ValidationError:
        logger.warning("demo_order_review_failed user_ref=%s profile_ref=%s symbol=%s error_category=invalid_proposal",
            user,profile_id,normalize_pair(symbol))
        return _demo_review_error("invalid_proposal","The order proposal schema is invalid.",
            symbol=symbol,order_type=order_type)
    except AutonomousExecutionError as exc:
        logger.warning("demo_order_review_failed user_ref=%s profile_ref=%s symbol=%s error_category=%s blocking_codes=%s",
            user,profile_id,normalize_pair(symbol),exc.code,",".join(exc.reasons))
        return _demo_review_error(exc.code,
            "The demo order preview was blocked by deterministic server validation.",exc.reasons,
            symbol=symbol,order_type=order_type,calculation=exc.details.get("calculation"),
            warnings=exc.details.get("warnings"))
    except Exception:
        logger.error("demo_order_review_failed user_ref=%s profile_ref=%s symbol=%s error_category=internal_review_error",
            user,profile_id,normalize_pair(symbol))
        return _demo_review_error("internal_review_error","The demo order preview is temporarily unavailable.",
            symbol=symbol,order_type=order_type)


def _demo_review_error(category:str,message:str,reasons:list[str]|None=None,*,
                       symbol:str|None=None,order_type:str|None=None,
                       calculation:dict[str,Any]|None=None,warnings:list[str]|None=None)->dict[str,Any]:
    codes=reasons or [category]
    blockers=[{"code":code,"message":DEMO_BLOCKING_MESSAGES.get(code,
        "A required server-side validation did not pass.")} for code in codes]
    return {"schema_version":"1.0","status":"blocked","error":category,
        "error_category":category,"message":message,"blocking_reasons":blockers,
        "preview_id":None,"expires_at":None,"submission_allowed":False,
        "calculation":calculation or {"requested_symbol":normalize_pair(symbol) if symbol else None,
            "order_type":order_type,"quantity":None,"estimated_risk":None,"estimated_margin":None},
        "warnings":warnings or [],"quantity":None,"estimated_risk":None,"estimated_margin":None}


async def submit_demo_order(preview_id:str,idempotency_key:str)->dict[str,Any]:
    try:return await AutonomousDemoService().submit(_authenticated_user(),preview_id,idempotency_key)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def get_demo_execution_result(execution_id:str)->dict[str,Any]:
    try:return await AutonomousDemoService().execution_result(_authenticated_user(),execution_id)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def review_cancel_demo_order(profile_id:str,order_id:str)->dict[str,Any]:
    try:return await AutonomousDemoService().review_action(_authenticated_user(),profile_id,"cancel_order",order_id)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def submit_cancel_demo_order(preview_id:str,idempotency_key:str)->dict[str,Any]:
    try:return await AutonomousDemoService().submit_action(_authenticated_user(),preview_id,idempotency_key)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def review_close_demo_position(profile_id:str,position_id:str)->dict[str,Any]:
    try:return await AutonomousDemoService().review_action(_authenticated_user(),profile_id,"close_position",position_id)
    except AutonomousExecutionError as exc:return exc.as_dict()


async def submit_close_demo_position(preview_id:str,idempotency_key:str)->dict[str,Any]:
    try:return await AutonomousDemoService().submit_action(_authenticated_user(),preview_id,idempotency_key)
    except AutonomousExecutionError as exc:return exc.as_dict()


def get_provider_capabilities() -> dict[str, Any]:
    """Return configured public-provider capability state without exposing secrets."""
    return {"finnhub_enabled": settings.finnhub_enabled, "finnhub": capability_status(), "fred_enabled": settings.fred_enabled}


# Legacy compatibility aliases for existing business tests.
async def get_my_tradelocker_accounts() -> dict[str, Any]:
    """Deprecated alias for get_tradelocker_accounts."""
    return await list_my_tradelocker_accounts()


async def get_my_tradelocker_account_status(account_alias: str | None = None) -> dict[str, Any]:
    """Deprecated alias for get_account_status."""
    return await get_account_status(account_alias)


async def list_my_tradelocker_connections() -> dict[str, Any]:
    """List the authenticated user's safe TradeLocker connection references."""
    user_sub = get_current_user_sub()
    if not user_sub: return {"status": "error", "error": "authentication_required"}
    connections=[]
    for item in BrokerRepository().list_connection_tree(user_sub):
        connections.append({
            "connection_ref":item["public_id"], "label":item["label"] or item["broker_name"],
            "broker":_display_broker(item["broker_name"] or item["server"]),
            "environment":_display_value(item["environment"]),
            "status":"Connected" if item["enabled"] else "Reauthentication Required",
            "is_default":item["is_default"], "accounts_count":item["account_count"],
            "last_verified_at":item["last_verified_at"],
            "accounts":[_safe_account_output(account) for account in item["accounts"]],
        })
    return {"status": "ok", "connections": connections}


async def list_my_tradelocker_accounts() -> dict[str, Any]:
    """List durable account aliases without exposing broker account identifiers."""
    user_sub = get_current_user_sub()
    if not user_sub: return {"status": "error", "error": "authentication_required"}
    return {"status": "ok", "accounts": [_safe_account_output(item) for item in BrokerRepository().list_accounts(user_sub)]}


async def list_execution_profiles() -> dict[str, Any]:
    """List safe execution-profile selectors for the authenticated user."""
    user_sub = get_current_user_sub()
    if not user_sub: return {"status": "error", "error": "authentication_required"}
    return {"status": "ok", "profiles": BrokerRepository().list_profiles(user_sub)}


async def get_my_tradelocker_symbols() -> dict[str, Any] | list[Any]:
    """Deprecated alias for get_tradelocker_symbols."""
    return await get_tradelocker_symbols()


async def get_my_tradelocker_quote(symbol: str) -> dict[str, Any] | list[Any]:
    """Deprecated alias for get_tradelocker_quote."""
    return await get_tradelocker_quote(symbol)


async def get_my_tradelocker_candles(
    symbol: str, timeframe: str, lookback: int | None = None,
    start_time: str | None = None, end_time: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias for get_market_candles using TradeLocker."""
    return await get_market_candles(symbol, timeframe, "tradelocker", lookback, start_time, end_time)
