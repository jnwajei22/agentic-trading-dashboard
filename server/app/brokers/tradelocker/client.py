import asyncio
from copy import deepcopy
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import jwt

from app.config.settings import settings

from app.services.market_data.history import (
    MAX_CANDLES,
    CANONICAL_TIMEFRAMES,
    TIMEFRAME_DURATION_MS,
    PaginatedCandleResult,
    aggregate_hourly_candles_to_utc_days,
    aggregate_complete_candles,
    get_candles_paginated,
    normalize_timeframe,
    validate_candle_result,
)
from app.services.market_data.candle_cache import CandleCacheEntry, CandleCacheKey, DurableCandleCache, candle_cache
from app.services.tradelocker.rate_limiter import TradeLockerRequestLimiter, tradelocker_request_limiter


SUPPORTED_TIMEFRAMES = set(TIMEFRAME_DURATION_MS)


class TradeLockerError(RuntimeError):
    """A sanitized, structured TradeLocker client failure."""

    def __init__(
        self,
        operation: str,
        message: str,
        *,
        code: str = "tradelocker_error",
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": self.code,
            "operation": self.operation,
            "status_code": self.status_code,
            "message": str(self),
            **self.details,
        }


def _not_implemented(operation: str) -> dict[str, str]:
    return {
        "status": "not_implemented",
        "operation": operation,
        "message": "The configured TradeLocker API does not expose this read endpoint.",
    }


class TradeLockerClient:
    """Async client for documented TradeLocker account and order operations."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str | None,
        password: str | None,
        server: str | None,
        account_id: str | None,
        account_number: str | None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_user_id: str | None = None,
        cache_connection_id: str | None = None,
        cache_account_record_id: str | None = None,
        candle_cache_store: DurableCandleCache = candle_cache,
        request_limiter: TradeLockerRequestLimiter = tradelocker_request_limiter,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.server = server
        self.account_id = account_id
        self.account_number = account_number
        self.cache_user_id = cache_user_id
        self.cache_connection_id = cache_connection_id
        self.cache_account_record_id = cache_account_record_id
        self.candle_cache = candle_cache_store
        self.request_limiter = request_limiter
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            transport=transport,
        )
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._login_lock = asyncio.Lock()
        self.token_refresh_count = 0
        self._instrument_cache: dict[str, tuple[Any, Any]] = {}
        self._history_attempts = 0
        self._history_retries = 0
        self._history_backoff_seconds = 0.0
        self._history_cooldown_until: str | None = None
        self._candle_inflight: dict[tuple[Any, ...], asyncio.Task[PaginatedCandleResult]] = {}

    async def __aenter__(self) -> "TradeLockerClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def validate_login_config(self, operation: str = "login") -> None:
        missing = [
            name
            for name, value in (
                ("TRADELOCKER_BASE_URL", self.base_url),
                ("TRADELOCKER_USERNAME", self.username),
                ("TRADELOCKER_PASSWORD", self.password),
                ("TRADELOCKER_SERVER", self.server),
            )
            if not value
        ]
        if missing:
            raise TradeLockerError(
                operation,
                f"TradeLocker login configuration is incomplete: {', '.join(missing)}.",
                code="not_configured",
            )

    def validate_account_config(self, operation: str = "account") -> None:
        self.validate_login_config(operation)
        missing = [
            name
            for name, value in (
                ("TRADELOCKER_ACCOUNT_ID", self.account_id),
                ("TRADELOCKER_ACCOUNT_NUMBER", self.account_number),
            )
            if not value
        ]
        if missing:
            raise TradeLockerError(
                operation,
                f"TradeLocker account configuration is incomplete: {', '.join(missing)}.",
                code="not_configured",
            )

    def _credentials(self) -> dict[str, str]:
        self.validate_login_config()
        return {"email": self.username, "password": self.password, "server": self.server}

    async def login(self, *, force: bool = False) -> str:
        if not force and self._access_token and time.time() < self._token_expires_at - 30:
            return self._access_token
        async with self._login_lock:
            if not force and self._access_token and time.time() < self._token_expires_at - 30:
                return self._access_token
            replacing_token = force and self._access_token is not None
            payload = await self._request(
                "POST", "/auth/jwt/token", operation="login", auth=False, json=self._credentials()
            )
            token = payload.get("accessToken") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise TradeLockerError(
                    "login", "TradeLocker did not return an access token.", code="invalid_response"
                )
            self._access_token = token
            self._token_expires_at = self._token_expiry(token, payload)
            if replacing_token:
                self.token_refresh_count += 1
            return token

    @staticmethod
    def _token_expiry(token: str, payload: dict[str, Any]) -> float:
        for key in ("accessTokenExpiresAt", "accessTokenExpiration", "expiresAt"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
            if isinstance(exp, (int, float)):
                return float(exp)
        except jwt.PyJWTError:
            pass
        return time.time() + 300

    def _account_headers(self, operation: str = "account") -> dict[str, str]:
        if not self.account_number:
            message = "TradeLocker account number is not configured."
            if operation == "get_config":
                message = (
                    "Account number is required for account-specific config. "
                    "Run get_tradelocker_accounts first."
                )
            raise TradeLockerError(
                operation, message, code="not_configured"
            )
        return {"accNum": self.account_number}

    def _account_path(self, suffix: str) -> str:
        if not self.account_id:
            raise TradeLockerError(
                "account", "TradeLocker account ID is not configured.", code="not_configured"
            )
        return f"/trade/accounts/{self.account_id}/{suffix.lstrip('/')}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if auth:
            headers["Authorization"] = f"Bearer {await self.login()}"
        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
            if auth and response.status_code == 401:
                headers["Authorization"] = f"Bearer {await self.login(force=True)}"
                response = await self._http.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise TradeLockerError(
                operation, "TradeLocker request timed out.", code="timeout"
            ) from None
        except httpx.HTTPStatusError as exc:
            retry_after: float | None = None
            header = exc.response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = max(0.0, float(header))
                except ValueError:
                    try:
                        retry_after = max(0.0, (
                            parsedate_to_datetime(header).astimezone(timezone.utc)
                            - datetime.now(timezone.utc)
                        ).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        retry_after = None
            raise TradeLockerError(
                operation,
                "TradeLocker rejected the request.",
                code="rate_limited" if exc.response.status_code == 429 else "http_error",
                status_code=exc.response.status_code,
                details={"retry_after_seconds": retry_after} if retry_after is not None else {},
            ) from None
        except (httpx.RequestError, ValueError) as exc:
            raise TradeLockerError(
                operation, "TradeLocker returned an unusable response.", code="request_failed"
            ) from None

    async def _optional_get(self, path: str, *, operation: str, **kwargs: Any) -> Any:
        try:
            return await self._request("GET", path, operation=operation, **kwargs)
        except TradeLockerError as exc:
            if exc.status_code in {404, 405, 501}:
                return _not_implemented(operation)
            raise

    async def get_config(self) -> Any:
        return await self._optional_get(
            "/trade/config",
            operation="get_config",
            headers=self._account_headers("get_config"),
        )

    async def get_accounts(self) -> Any:
        self.validate_login_config("get_accounts")
        payload = await self._optional_get(
            "/auth/jwt/all-accounts", operation="get_accounts"
        )
        if isinstance(payload, dict) and payload.get("status") == "not_implemented":
            return payload
        container = payload.get("d", payload) if isinstance(payload, dict) else {}
        records = container.get("accounts", []) if isinstance(container, dict) else []
        if not isinstance(records, list):
            raise TradeLockerError(
                "get_accounts",
                "TradeLocker returned an unusable accounts response.",
                code="invalid_response",
            )
        safe_records = []
        for record in records:
            if not isinstance(record, dict):
                continue
            safe_record = {}
            account_id = record.get("accountId", record.get("id"))
            if account_id is not None:
                safe_record["accountId"] = account_id
            for key in ("accNum", "name", "currency", "status"):
                if key in record:
                    safe_record[key] = record[key]
            safe_records.append(safe_record)
        return {"accounts": safe_records}

    async def get_account_state_payload(self) -> Any:
        """Fetch the internal positional state payload; callers must map it with /trade/config."""
        return await self._optional_get(
            self._account_path("state"),
            operation="get_account_state",
            headers=self._account_headers(),
        )

    async def get_open_positions(self) -> Any:
        return await self._optional_get(
            self._account_path("positions"),
            operation="get_open_positions",
            headers=self._account_headers(),
        )

    async def get_orders(self) -> Any:
        return await self._optional_get(
            self._account_path("orders"),
            operation="get_orders",
            headers=self._account_headers(),
        )

    async def get_symbols(self) -> Any:
        return await self._optional_get(
            self._account_path("instruments"),
            operation="get_symbols",
            headers=self._account_headers(),
        )

    @staticmethod
    def _instrument_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("instruments", "data", "d"):
                nested = payload.get(key)
                rows = TradeLockerClient._instrument_rows(nested)
                if rows:
                    return rows
        return []

    async def resolve_instrument(self, symbol: str, *, route_type: str = "INFO") -> tuple[Any, Any, dict[str, Any]]:
        payload = await self.get_symbols()
        if isinstance(payload, dict) and payload.get("status") == "not_implemented":
            raise TradeLockerError(
                "resolve_symbol", "TradeLocker instruments are unavailable.", code="not_implemented"
            )
        target = symbol.replace("/", "").upper()
        for row in self._instrument_rows(payload):
            name = str(row.get("name", row.get("symbol", ""))).replace("/", "").upper()
            if name != target:
                continue
            instrument_id = row.get("tradableInstrumentId", row.get("id"))
            routes = row.get("routes", [])
            route_id = next(
                (
                    route.get("id", route.get("routeId"))
                    for route in routes
                    if isinstance(route, dict) and str(route.get("type", "")).upper() == route_type.upper()
                ),
                row.get("routeId"),
            )
            if instrument_id is not None and route_id is not None:
                return instrument_id, route_id, row
        raise TradeLockerError(
            "resolve_symbol", "The requested TradeLocker symbol was not found.", code="symbol_not_found"
        )

    async def _resolve_instrument(self, symbol: str) -> tuple[Any, Any]:
        key = symbol.replace("/", "").upper()
        if key in self._instrument_cache:
            return self._instrument_cache[key]
        instrument_id, route_id, _ = await self.resolve_instrument(symbol)
        self._instrument_cache[key] = (instrument_id, route_id)
        return instrument_id, route_id

    async def get_instrument_details(self, symbol: str) -> dict[str, Any]:
        instrument_id, info_route, listing = await self.resolve_instrument(symbol, route_type="INFO")
        payload = await self._optional_get(
            f"/trade/instruments/{instrument_id}", operation="get_instrument_details",
            headers=self._account_headers(), params={"routeId": info_route},
        )
        if not isinstance(payload, dict):
            raise TradeLockerError("get_instrument_details", "TradeLocker instrument metadata is unusable.", code="invalid_response")
        return {"instrument_id": instrument_id, "info_route_id": info_route, "listing": listing, "details": payload}

    async def get_orders_history(self) -> Any:
        return await self._optional_get(
            self._account_path("ordersHistory"), operation="get_orders_history",
            headers=self._account_headers(),
        )

    async def place_order(self, order: dict[str, Any]) -> Any:
        """Place one order. Callers must provide a server-validated immutable payload."""
        allowed = {
            "qty", "routeId", "side", "validity", "type", "tradableInstrumentId",
            "price", "stopPrice", "stopLoss", "stopLossType", "takeProfit", "takeProfitType", "strategyId",
        }
        if set(order) - allowed:
            raise TradeLockerError("place_order", "The TradeLocker order contains unsupported fields.", code="invalid_order")
        required = {"qty", "routeId", "side", "validity", "type", "tradableInstrumentId", "stopLoss", "takeProfit"}
        if not required.issubset(order):
            raise TradeLockerError("place_order", "The TradeLocker order is incomplete.", code="invalid_order")
        return await self._request(
            "POST", self._account_path("orders"), operation="place_order",
            headers=self._account_headers(), json=order,
        )

    async def cancel_order(self, order_id: str) -> Any:
        """Cancel one specific non-final order on the already-scoped account."""
        if not str(order_id).strip():
            raise TradeLockerError("cancel_order", "A broker order identifier is required.", code="invalid_order")
        return await self._request("DELETE", f"/trade/orders/{order_id}", operation="cancel_order", headers=self._account_headers())

    async def close_position(self, position_id: str, *, strategy_id: str) -> Any:
        """Request a full close (qty=0) for one position on the scoped account."""
        if not str(position_id).strip():
            raise TradeLockerError("close_position", "A broker position identifier is required.", code="invalid_position")
        return await self._request("DELETE", f"/trade/positions/{position_id}", operation="close_position",
            headers=self._account_headers(), params={"strategyId": strategy_id}, json={"qty": 0})

    async def get_quote(self, symbol: str) -> Any:
        instrument_id, route_id = await self._resolve_instrument(symbol)
        return await self._optional_get(
            "/trade/quotes",
            operation="get_quote",
            headers=self._account_headers(),
            params={"tradableInstrumentId": instrument_id, "routeId": route_id},
        )

    async def _history_page(
        self,
        *,
        instrument_id: Any,
        route_id: Any,
        resolution: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> Any:
        """Fetch one bounded page through the connection-wide durable limiter."""
        connection_scope = self.cache_connection_id or ":".join((
            self.base_url, self.server or "", self.account_id or "", self.account_number or ""
        ))
        maximum_attempts = settings.tradelocker_rate_limit_max_retries + 1
        for attempt in range(maximum_attempts):
            lease = await self.request_limiter.acquire(connection_scope)
            self._history_backoff_seconds += lease.waited_seconds
            self._history_attempts += 1
            try:
                return await self._request(
                    "GET",
                    "/trade/history",
                    operation="get_candles",
                    headers=self._account_headers(),
                    params={
                        "tradableInstrumentId": instrument_id,
                        "routeId": route_id,
                        "resolution": resolution,
                        "from": start_time_ms,
                        "to": end_time_ms,
                    },
                )
            except TradeLockerError as exc:
                transient = exc.code in {"timeout", "request_failed"} or exc.status_code in {
                    429, 500, 502, 503, 504
                }
                if not transient:
                    raise
                if exc.status_code == 429:
                    retry_after = float(exc.details.get("retry_after_seconds") or 0)
                    exponential = min(
                        settings.tradelocker_rate_limit_max_backoff_seconds,
                        (2**attempt) + random.uniform(0, 0.25),
                    )
                    delay = max(retry_after, exponential)
                    cooldown_epoch = self.request_limiter.set_cooldown(connection_scope, delay)
                    self._history_cooldown_until = datetime.fromtimestamp(
                        cooldown_epoch, timezone.utc
                    ).isoformat()
                else:
                    delay = min(settings.tradelocker_rate_limit_max_backoff_seconds,
                                0.25 * (2**attempt) + random.uniform(0, 0.1))
                if attempt == maximum_attempts - 1:
                    code = "tradelocker_rate_limit_exhausted" if exc.status_code == 429 else exc.code
                    raise TradeLockerError(
                        "get_candles", "TradeLocker candle retrieval is temporarily unavailable.",
                        code=code, status_code=exc.status_code,
                        details={"retryable": True, "suggested_retry_at": self._history_cooldown_until,
                                 "attempts": self._history_attempts,
                                 "retry_count": self._history_retries,
                                 "cooldown_until": self._history_cooldown_until,
                                 "total_backoff_seconds": round(self._history_backoff_seconds, 3)},
                    ) from None
                if exc.status_code == 429 and delay > settings.tradelocker_rate_limit_max_backoff_seconds:
                    raise TradeLockerError(
                        "get_candles", "TradeLocker requested a cooldown longer than the bounded retry window.",
                        code="tradelocker_rate_limit_exhausted", status_code=429,
                        details={"retryable": True, "suggested_retry_at": self._history_cooldown_until,
                                 "attempts": self._history_attempts,
                                 "retry_count": self._history_retries,
                                 "cooldown_until": self._history_cooldown_until,
                                 "total_backoff_seconds": round(self._history_backoff_seconds, 3)},
                    ) from None
                self._history_retries += 1
                self._history_backoff_seconds += delay
                await asyncio.sleep(delay)
            finally:
                self.request_limiter.release(lease)
        raise AssertionError("unreachable")

    def _candle_cache_key(
        self, *, instrument_id: Any, symbol: str, resolution: str
    ) -> CandleCacheKey | None:
        if not all((self.cache_user_id, self.cache_connection_id, self.account_id,
                    self.account_number, self.cache_account_record_id)):
            return None
        return CandleCacheKey(
            user_id=str(self.cache_user_id), connection_id=str(self.cache_connection_id),
            account_id=str(self.account_id), account_number=str(self.account_number),
            instrument_id=str(instrument_id), symbol=symbol, timeframe=resolution,
        )

    def _cached_candle_result(
        self, entry: CandleCacheEntry, *, symbol: str, timeframe: str,
        requested_count: int | None, minimum_usable: int, end_ms: int,
        start_ms: int | None = None,
        fresh: bool, rate_limited: bool = False,
    ) -> PaginatedCandleResult:
        candles = [
            candle for candle in entry.candles
            if (start_ms is None or candle.timestamp >= start_ms)
            and candle.timestamp <= end_ms
        ]
        result = PaginatedCandleResult(
            instrument_id=entry.key.instrument_id, timeframe=entry.key.timeframe,
            requested_start_ms=(start_ms if start_ms is not None else
                                (candles[0].timestamp if candles else entry.candles[0].timestamp)),
            requested_end_ms=end_ms,
            estimated_candles=requested_count or len(candles), candles=candles,
            batches_requested=0, complete=True, stop_reason="cache_hit",
            requested_timeframe=timeframe, provider_timeframe_sent=entry.key.timeframe,
            rows_received=len(candles), raw_count=len(candles), source=entry.source,
            cache_hit=True, cache_fresh=fresh,
            cache_age_seconds=round(entry.age_seconds(datetime.now(timezone.utc)), 3),
            upstream_request_made=False,
        )
        result = validate_candle_result(
            result, symbol=symbol, requested_timeframe=timeframe,
            requested_count=requested_count, minimum_usable=minimum_usable, now_ms=end_ms,
        )
        if rate_limited:
            result.warnings = list(dict.fromkeys(
                [*result.warnings, "cached_candles_used_after_rate_limit"]
            ))
            result.status = "partial" if result.complete else "blocked"
        return result

    def _decorate_and_cache(
        self, result: PaginatedCandleResult, key: CandleCacheKey | None
    ) -> PaginatedCandleResult:
        result.attempts = self._history_attempts
        result.retry_count = self._history_retries
        result.total_backoff_seconds = round(self._history_backoff_seconds, 3)
        result.cooldown_until = self._history_cooldown_until
        result.upstream_request_made = self._history_attempts > 0
        if key is not None and result.complete and result.usable_candles:
            metadata = result.canonical_dict()["metadata"]
            self.candle_cache.put(
                key, result.usable_candles,
                source="aggregated" if result.source.startswith("aggregated") else "direct",
                metadata=metadata,
            )
        return result

    def _rate_limit_cache_fallback(
        self, exc: TradeLockerError, cached: CandleCacheEntry | None, *, symbol: str,
        timeframe: str, requested_count: int | None, minimum_usable: int, end_ms: int,
        start_ms: int | None = None,
    ) -> PaginatedCandleResult | None:
        if exc.code != "tradelocker_rate_limit_exhausted" or cached is None:
            return None
        age = cached.age_seconds(datetime.now(timezone.utc))
        if age > settings.tradelocker_candle_cache_max_stale_seconds:
            exc.details.update({"cache_available": True, "cache_age_seconds": round(age, 3),
                                "cache_rejection_reason": "cache_exceeds_stale_tolerance"})
            return None
        if not cached.covers(
            required_count=minimum_usable, start_time_ms=start_ms, end_time_ms=end_ms
        ):
            rejection = (
                "cached_range_not_covered" if start_ms is not None
                else "cached_history_insufficient"
            )
            exc.details.update({"cache_available": True, "cache_age_seconds": round(age, 3),
                                "cache_rejection_reason": rejection})
            return None
        result = self._cached_candle_result(
            cached, symbol=symbol, timeframe=timeframe, requested_count=requested_count,
            minimum_usable=minimum_usable, end_ms=end_ms, start_ms=start_ms,
            fresh=False, rate_limited=True,
        )
        if not result.complete:
            rejection = (
                "cached_range_not_covered" if start_ms is not None
                else "cached_history_insufficient"
            )
            exc.details.update({"cache_available": True, "cache_age_seconds": round(age, 3),
                                "cache_rejection_reason": rejection})
            return None
        result.attempts = self._history_attempts
        result.retry_count = self._history_retries
        result.total_backoff_seconds = round(self._history_backoff_seconds, 3)
        result.cooldown_until = self._history_cooldown_until
        result.upstream_request_made = True
        return result

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        lookback: int | None = 300,
        *,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        minimum_usable: int | None = None,
    ) -> PaginatedCandleResult:
        try:
            coalesced_timeframe = normalize_timeframe(timeframe)
        except ValueError:
            coalesced_timeframe = timeframe.upper()
        request_key = (symbol.replace("/", "").upper(), coalesced_timeframe, lookback,
                       start_time_ms, end_time_ms, minimum_usable)
        existing = self._candle_inflight.get(request_key)
        if existing is not None:
            result = deepcopy(await existing)
            result.coalesced_requests += 1
            return result
        task = asyncio.create_task(self._get_candles_impl(
            symbol, timeframe, lookback, start_time_ms=start_time_ms,
            end_time_ms=end_time_ms, minimum_usable=minimum_usable,
        ))
        self._candle_inflight[request_key] = task
        try:
            return await task
        finally:
            self._candle_inflight.pop(request_key, None)

    async def _get_candles_impl(
        self,
        symbol: str,
        timeframe: str,
        lookback: int | None = 300,
        *,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        minimum_usable: int | None = None,
    ) -> PaginatedCandleResult:
        try:
            resolution = normalize_timeframe(timeframe)
        except ValueError:
            raise TradeLockerError(
                "get_candles", "Unsupported TradeLocker timeframe.", code="unsupported_timeframe",
                details={"requested_timeframe": timeframe,
                         "supported_internal_values": list(CANONICAL_TIMEFRAMES),
                         "provider_value_attempted": None,
                         "error_category": "unsupported_timeframe"},
            ) from None
        if lookback is not None and not 1 <= lookback <= MAX_CANDLES:
            raise TradeLockerError(
                "get_candles", f"Lookback must be between 1 and {MAX_CANDLES}.", code="invalid_lookback"
            )
        instrument_id, route_id = await self._resolve_instrument(symbol)
        end_ms = end_time_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        self._history_attempts = self._history_retries = 0
        self._history_backoff_seconds = 0.0
        self._history_cooldown_until = None
        cache_key = self._candle_cache_key(
            instrument_id=instrument_id, symbol=symbol, resolution=resolution
        )
        cached = self.candle_cache.get(cache_key) if cache_key is not None else None
        required = minimum_usable or lookback or 1
        requested_start_ms = start_time_ms
        if (cached is not None and cached.fresh(datetime.now(timezone.utc))
                and cached.covers(required_count=required, start_time_ms=requested_start_ms,
                                  end_time_ms=end_ms)):
            candidate = self._cached_candle_result(
                cached, symbol=symbol, timeframe=timeframe, requested_count=lookback,
                minimum_usable=required, end_ms=end_ms, start_ms=requested_start_ms,
                fresh=True,
            )
            if candidate.complete:
                return candidate
        effective_count = lookback if start_time_ms is None else None
        if start_time_ms is None:
            effective_count = lookback or 300
            # A candle count is not a calendar-duration request. Widen daily
            # lookbacks for normal forex closures; the paginator still returns
            # exactly the requested number of broker rows.
            range_count = effective_count * 2 if resolution == "1D" else effective_count
            start_time_ms = end_ms - TIMEFRAME_DURATION_MS[resolution] * range_count
            cached_requested_count = int(cached.metadata.get("requested_count") or 0) if cached else 0
            if (cached is not None and len(cached.candles) >= required
                    and (cached_requested_count >= effective_count
                         or len(cached.candles) >= effective_count)):
                # Include one overlap candle so incremental refresh can safely
                # merge and deduplicate without downloading the full window.
                start_time_ms = max(
                    start_time_ms,
                    cached.newest_completed_timestamp - TIMEFRAME_DURATION_MS[resolution],
                )
        if start_time_ms >= end_ms:
            raise TradeLockerError(
                "get_candles", "start_time must be earlier than end_time.", code="invalid_time_range"
            )

        async def fetch_page(page_start: int, page_end: int) -> Any:
            return await self._history_page(
                instrument_id=instrument_id, route_id=route_id, resolution=resolution,
                start_time_ms=page_start, end_time_ms=page_end,
            )

        direct_error: TradeLockerError | None = None
        try:
            direct = await get_candles_paginated(
                instrument_id=str(instrument_id), timeframe=resolution,
                start_time_ms=start_time_ms, end_time_ms=end_ms,
                requested_count=effective_count, fetch_page=fetch_page,
            )
            if cached is not None:
                direct.candles = sorted(
                    {row.timestamp: row for row in [*cached.candles, *direct.candles]}.values(),
                    key=lambda row: row.timestamp,
                )[-(lookback or MAX_CANDLES):]
                direct.duplicate_count += len(cached.candles) + direct.rows_received - len(direct.candles)
            direct.requested_timeframe = timeframe
            direct.provider_timeframe_sent = resolution
            required = minimum_usable or effective_count or 1
            direct = validate_candle_result(
                direct, symbol=symbol, requested_timeframe=timeframe,
                requested_count=effective_count, minimum_usable=required,
                now_ms=end_ms,
            )
            if direct.complete or resolution not in {"1D", "4H"} or effective_count is None:
                return self._decorate_and_cache(direct, cache_key)
        except TradeLockerError as exc:
            fallback = self._rate_limit_cache_fallback(
                exc, cached, symbol=symbol, timeframe=timeframe,
                requested_count=lookback, minimum_usable=required, end_ms=end_ms,
                start_ms=requested_start_ms,
            )
            if fallback is not None:
                return fallback
            if exc.code == "tradelocker_rate_limit_exhausted":
                exc.details.setdefault("cache_available", cached is not None)
                exc.details.setdefault("cache_age_seconds", round(cached.age_seconds(datetime.now(timezone.utc)), 3) if cached else None)
                exc.details.setdefault("cache_rejection_reason", "cache_missing" if cached is None else "cached_history_insufficient")
                raise
            if resolution not in {"1D", "4H"} or effective_count is None:
                raise
            direct_error = exc
        except ValueError as exc:
            raise TradeLockerError("get_candles", str(exc), code="invalid_candle_request") from None

        # TradeLocker documents 1D, but some broker integrations do not expose
        # enough direct daily rows. Fall back only to the same account,
        # instrument and INFO route, and aggregate verified 1H broker bars.
        assert effective_count is not None
        fallback_span = effective_count * 2 * TIMEFRAME_DURATION_MS[resolution]
        fallback_start = end_ms - fallback_span
        fallback_diagnostics: dict[str, Any]

        async def fetch_hourly(page_start: int, page_end: int) -> Any:
            return await self._history_page(
                instrument_id=instrument_id, route_id=route_id, resolution="1H",
                start_time_ms=page_start, end_time_ms=page_end,
            )

        try:
            hourly = await get_candles_paginated(
                instrument_id=str(instrument_id), timeframe="1H",
                start_time_ms=fallback_start, end_time_ms=end_ms,
                requested_count=None, fetch_page=fetch_hourly,
            )
            hourly.requested_timeframe = timeframe
            hourly.provider_timeframe_sent = "1H"
            if resolution == "1D":
                aggregated_rows, incomplete_days = aggregate_hourly_candles_to_utc_days(
                    hourly.candles, required_count=effective_count
                )
            else:
                aggregated_rows, incomplete_days = aggregate_complete_candles(
                    hourly.candles, source_timeframe="1H", target_timeframe="4H",
                    required_count=effective_count,
                )
            fallback_diagnostics = hourly.diagnostics()
            fallback_diagnostics["incomplete_utc_days"] = incomplete_days
            fallback_diagnostics["complete_aggregate_rows"] = len(aggregated_rows)
            if resolution == "1D":
                fallback_diagnostics["complete_daily_rows"] = len(aggregated_rows)
        except (TradeLockerError, ValueError) as exc:
            if isinstance(exc, TradeLockerError) and exc.code == "tradelocker_rate_limit_exhausted":
                fallback = self._rate_limit_cache_fallback(
                    exc, cached, symbol=symbol, timeframe=timeframe,
                    requested_count=lookback, minimum_usable=required, end_ms=end_ms,
                    start_ms=requested_start_ms,
                )
                if fallback is not None:
                    return fallback
                exc.details.setdefault("cache_available", cached is not None)
                exc.details.setdefault(
                    "cache_age_seconds",
                    round(cached.age_seconds(datetime.now(timezone.utc)), 3) if cached else None,
                )
                exc.details.setdefault(
                    "cache_rejection_reason",
                    "cache_missing" if cached is None else "cached_history_insufficient",
                )
                raise
            fallback_diagnostics = {
                "requested_timeframe": timeframe,
                "provider_timeframe_sent": "1H",
                "http_status": getattr(exc, "status_code", None),
                "broker_error_category": getattr(exc, "code", "invalid_candle_request"),
                "rows_received": 0,
                "mapping_failure": None,
                "candle_source": "aggregated_1H",
            }
            aggregated_rows = []

        if direct_error is not None:
            direct_diagnostics = {
                "requested_timeframe": timeframe,
                "provider_timeframe_sent": resolution,
                "http_status": direct_error.status_code,
                "broker_error_category": direct_error.code,
                "rows_received": 0,
                "mapping_failure": None,
                "candle_source": "direct",
            }
        else:
            direct_diagnostics = direct.diagnostics()

        required = minimum_usable or effective_count
        if len(aggregated_rows) >= required:
            aggregated = PaginatedCandleResult(
                instrument_id=str(instrument_id), timeframe=resolution,
                requested_start_ms=fallback_start, requested_end_ms=end_ms,
                estimated_candles=effective_count, candles=aggregated_rows,
                batches_requested=hourly.batches_requested, complete=True,
                stop_reason="aggregated_complete_utc_days", requested_timeframe=timeframe,
                provider_timeframe_sent="1H", rows_received=len(aggregated_rows),
                source="aggregated_1H", fallback_diagnostics=direct_diagnostics,
                aggregation_source_timeframe="1H",
                incomplete_days_excluded=incomplete_days,
            )
            aggregated = validate_candle_result(
                aggregated, symbol=symbol, requested_timeframe=timeframe,
                requested_count=effective_count, minimum_usable=minimum_usable or effective_count,
                now_ms=end_ms,
            )
            return self._decorate_and_cache(aggregated, cache_key)

        incomplete = PaginatedCandleResult(
            instrument_id=str(instrument_id), timeframe=resolution,
            requested_start_ms=fallback_start, requested_end_ms=end_ms,
            estimated_candles=effective_count, candles=aggregated_rows,
            batches_requested=hourly.batches_requested if "hourly" in locals() else 0,
            complete=False, warning="Complete TradeLocker candles are unavailable.",
            stop_reason="incomplete_aggregation", requested_timeframe=timeframe,
            provider_timeframe_sent=resolution, http_status=direct_diagnostics["http_status"],
            broker_error_category=direct_diagnostics["broker_error_category"] or "incomplete_history",
            rows_received=direct_diagnostics["rows_received"],
            mapping_failure=direct_diagnostics["mapping_failure"], source="direct",
            fallback_diagnostics=fallback_diagnostics,
            aggregation_source_timeframe="1H",
            incomplete_days_excluded=fallback_diagnostics.get("incomplete_utc_days", []),
        )
        incomplete = validate_candle_result(
            incomplete, symbol=symbol, requested_timeframe=timeframe,
            requested_count=effective_count, minimum_usable=minimum_usable or effective_count,
            now_ms=end_ms,
        )
        return self._decorate_and_cache(incomplete, cache_key)
