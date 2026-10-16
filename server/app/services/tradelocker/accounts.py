from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import logging

from app.storage.brokers import BrokerRepository, BrokerStorageError


class AccountResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AccountResolutionMode(StrEnum):
    DEFAULT_READ = "default_read"
    EXPLICIT_ACCOUNT = "explicit_account"
    EXECUTION_PROFILE = "execution_profile"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrokerAccountContext:
    auth0_user_id: str
    connection_id: str
    connection_ref: str
    account_record_id: str
    account_alias: str
    account_id: str
    account_number: str
    account_name: str | None
    currency: str | None
    environment: str
    server: str
    active: bool
    base_url: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    broker_name: str | None = None
    connection_label: str | None = None
    demo_classification: str = "unknown"
    profile_ref: str | None = None

    def safe_identity(self) -> dict:
        return {
            "account_alias": self.account_alias,
            "account_id": self.account_record_id,
            "connection_id": self.connection_ref,
            "name": self.account_name,
            "currency": self.currency,
            "environment": self.environment,
            "active": self.active,
        }


class BrokerAccountResolver:
    def __init__(self, repository: BrokerRepository | None = None) -> None:
        self.repository = repository or BrokerRepository()

    def resolve(
        self,
        auth0_user_id: str,
        *,
        account_alias: str | None = None,
        account_ref: str | None = None,
        profile: str | None = None,
        require_available: bool = True,
    ) -> BrokerAccountContext:
        selectors = sum(value is not None for value in (account_alias, account_ref, profile))
        if selectors > 1:
            raise AccountResolutionError("invalid_account_selector", "Choose one account alias, account reference, or profile.")
        mode = AccountResolutionMode.EXECUTION_PROFILE if profile else AccountResolutionMode.EXPLICIT_ACCOUNT if selectors else AccountResolutionMode.DEFAULT_READ
        try:
            row = self.repository.get_account_record(auth0_user_id, alias=account_alias, account_ref=account_ref, profile=profile)
        except BrokerStorageError as exc:
            raise AccountResolutionError("broker_storage_error", "Stored broker account data is unavailable.") from exc
        if row is None:
            code = "account_alias_not_found" if account_alias or account_ref else "profile_not_found" if profile else "default_account_required"
            raise AccountResolutionError(code, "No owned TradeLocker account matches that selector.")
        if profile and (not row["profile_enabled"] or row["profile_execution_mode"] == "disabled"):
            raise AccountResolutionError("profile_disabled", "The selected execution profile is disabled.")
        if row["connection_status"] != "active" or not row["locally_enabled"]:
            raise AccountResolutionError("account_disabled", "The selected TradeLocker account is disabled.")
        if require_available and (not row["available"] or not row["broker_active"]):
            raise AccountResolutionError("account_unavailable", "The selected TradeLocker account is unavailable.")
        try:
            password = self.repository._cipher().decrypt(row["password_encrypted"], row["encryption_key_version"])
        except Exception as exc:
            raise AccountResolutionError("broker_storage_error", "Stored broker credentials cannot be decrypted.") from exc
        context = BrokerAccountContext(
            auth0_user_id=auth0_user_id,
            connection_id=str(row["connection_id"]),
            connection_ref=row["connection_ref"],
            account_record_id=row["public_id"],
            account_alias=row["account_alias"],
            account_id=row["broker_account_id"],
            account_number=row["acc_num"],
            account_name=row["account_name"],
            currency=row["currency"],
            environment=row["environment"],
            server=row["server"],
            active=bool(row["broker_active"]),
            broker_name=row["broker_name"], connection_label=row["connection_label"],
            demo_classification="demo" if row["is_demo"] == 1 else "live" if row["is_demo"] == 0 else "unknown",
            profile_ref=row["profile_ref"] if profile else None,
            base_url=row["base_url"], username=row["username"], password=password,
        )
        logger.info(
            "account_resolved user_ref=%s mode=%s profile_ref=%s account_ref=%s alias=%s connection_ref=%s environment=%s classification=%s",
            auth0_user_id, mode.value, context.profile_ref, context.account_record_id,
            context.account_alias, context.connection_ref, context.environment, context.demo_classification,
        )
        return context
