from app.brokers.base import BrokerAdapter
from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.config.settings import settings
from app.auth.identity import get_current_user_sub
from app.storage.brokers import BrokerRepository, BrokerStorageError
from app.models.orders import OrderPreview
from app.services.market_data.history import PaginatedCandleResult
from app.services.tradelocker.account_status import TradeLockerAccountStatusService
from app.services.tradelocker.accounts import AccountResolutionError, BrokerAccountResolver


class TradeLockerAdapter(BrokerAdapter):
    def __init__(self, client: TradeLockerClient | None = None) -> None:
        self.client = client or TradeLockerClient(
            base_url=settings.tradelocker_base_url,
            username=settings.tradelocker_username,
            password=settings.tradelocker_password,
            server=settings.tradelocker_server,
            account_id=settings.tradelocker_account_id,
            account_number=settings.tradelocker_account_number,
        )

    async def get_account(self) -> dict:
        user_sub = get_current_user_sub()
        if not user_sub:
            raise TradeLockerError(
                "get_account", "An authenticated user is required.", code="authentication_required"
            )
        return (await TradeLockerAccountStatusService().retrieve(user_sub)).model_dump(mode="json")

    async def get_open_positions(self) -> list[dict] | dict:
        return await self.client.get_open_positions()

    async def get_quote(self, pair: str) -> dict:
        return await self.client.get_quote(pair)

    async def get_candles(
        self, pair: str, timeframe: str, lookback: int | None = 300, *,
        start_time_ms: int | None = None, end_time_ms: int | None = None,
        minimum_usable: int | None = None,
    ) -> PaginatedCandleResult:
        user_sub = get_current_user_sub()
        if user_sub and not getattr(self.client, "cache_user_id", None):
            try:
                context = BrokerAccountResolver().resolve(user_sub)
            except AccountResolutionError as exc:
                raise TradeLockerError("broker_connection", str(exc), code=exc.code) from None
            scoped = TradeLockerClient(
                base_url=context.base_url, username=context.username,
                password=context.password, server=context.server,
                account_id=context.account_id, account_number=context.account_number,
                cache_user_id=user_sub, cache_connection_id=context.connection_ref,
                cache_account_record_id=context.account_record_id,
            )
            try:
                return await scoped.get_candles(
                    pair, timeframe, lookback, start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms, minimum_usable=minimum_usable,
                )
            finally:
                await scoped.aclose()
        return await self.client.get_candles(
            pair, timeframe, lookback, start_time_ms=start_time_ms,
            end_time_ms=end_time_ms, minimum_usable=minimum_usable,
        )

    async def submit_order(self, preview: OrderPreview) -> dict:
        raise NotImplementedError("Live TradeLocker execution is intentionally disabled.")


def get_tradelocker_adapter() -> TradeLockerAdapter:
    user_sub = get_current_user_sub()
    if user_sub:
        try:
            connection = BrokerRepository().get_connection(user_sub)
        except BrokerStorageError as exc:
            raise TradeLockerError(
                "broker_connection", str(exc), code="broker_storage_error"
            ) from None
        if connection:
            return TradeLockerAdapter(
                TradeLockerClient(
                    base_url=connection.base_url,
                    username=connection.username,
                    password=connection.password,
                    server=connection.server,
                    account_id=connection.account_id,
                    account_number=connection.account_number,
                )
            )
        raise TradeLockerError(
            "broker_connection",
            "TradeLocker connection setup is required for the current user.",
            code="setup_required",
        )
    if settings.allow_env_broker_fallback:
        return TradeLockerAdapter()
    raise TradeLockerError(
        "broker_connection",
        "TradeLocker connection setup is required for the current user.",
        code="setup_required",
    )
