from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from app.config.settings import settings
from app.jobs.autonomous_scheduler import AutonomousScheduleService, dispatch_autonomous_cycle
from app.services.autonomous.decision import decision_provider_readiness
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository


Environment = Literal["demo", "live"]
REQUIRED_RISK_FIELDS = {
    "risk_per_trade_percent", "daily_loss_limit_percent", "drawdown_cutoff_percent",
    "maximum_open_positions", "maximum_pending_orders", "maximum_new_entries_per_day",
    "minimum_reward_risk",
}


class AutonomousControlError(RuntimeError):
    def __init__(self, code: str, message: str, *, reasons: list[str] | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reasons = reasons or [code]
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"status": "blocked", "error": self.code, "message": str(self),
            "blocking_reasons": self.reasons, **self.details}


def strategy_default_times(profile: dict[str, Any]) -> list[str]:
    """Hourly UTC evaluations covering the profile's configured forex sessions."""
    sessions = set(profile.get("allowed_sessions") or ["london", "new_york", "overlap"])
    hours: set[int] = set()
    if "london" in sessions: hours.update(range(7, 16))
    if "new_york" in sessions: hours.update(range(12, 21))
    if "overlap" in sessions: hours.update(range(12, 16))
    if not hours: hours.update(range(7, 21))
    return [f"{hour:02d}:00" for hour in sorted(hours)]


class AutonomyControlService:
    def __init__(self, *, brokers: BrokerRepository | None = None,
                 execution: ExecutionRepository | None = None,
                 schedules: ScheduleRepository | None = None,
                 runner_factory: Callable[[], AutonomousDecisionRunner] | None = None) -> None:
        self.brokers = brokers or BrokerRepository()
        self.execution = execution or ExecutionRepository()
        self.schedules = schedules or ScheduleRepository()
        self.schedule_service = AutonomousScheduleService(
            schedules=self.schedules, brokers=self.brokers, execution=self.execution)
        self.runner_factory = runner_factory or (lambda: AutonomousDecisionRunner(
            brokers=self.brokers, execution=self.execution))

    def _account(self, user_sub: str, environment: Environment,
                 account_alias: str | None) -> dict[str, Any]:
        accounts = self.brokers.list_accounts(user_sub)
        if account_alias:
            matches = [item for item in accounts if item["account_alias"].casefold() == account_alias.casefold()]
            if not matches:
                raise AutonomousControlError("account_not_found", "No owned account matches that alias.")
            account = matches[0]
        else:
            matches = [item for item in accounts if item.get("is_default_analysis")]
            if not matches:
                raise AutonomousControlError("selected_account_not_found", "Select an account before starting autonomy.")
            account = matches[0]
        expected_demo = 1 if environment == "demo" else 0
        if account.get("environment") != environment or account.get("is_demo") != expected_demo:
            raise AutonomousControlError("account_environment_mismatch",
                f"The selected account is not a verified {environment} account.")
        connection = next((item for item in self.brokers.list_connections(user_sub)
            if item["public_id"] == account["connection_id"]), None)
        if not connection or not connection.get("enabled"):
            raise AutonomousControlError("broker_connection_inactive", "The account's broker connection is inactive.")
        if not account.get("available") or not account.get("locally_enabled") or not account.get("broker_active"):
            raise AutonomousControlError("account_unavailable", "The requested broker account is unavailable.")
        return account

    def _profile(self, user_sub: str, account: dict[str, Any], profile_ref: str | None) -> dict[str, Any]:
        profiles = self.brokers.list_profiles(user_sub)
        if profile_ref:
            profile = next((item for item in profiles if item["public_id"] == profile_ref), None)
            if not profile:
                raise AutonomousControlError("profile_not_found", "No owned execution profile matches that reference.")
            if profile.get("account_id") != account["public_id"]:
                raise AutonomousControlError("profile_account_mismatch", "The profile is not bound to the resolved account.")
            matches = [profile] if profile.get("enabled") else []
        else:
            matches = [item for item in profiles
                if item.get("account_id") == account["public_id"] and item.get("enabled")]
        if not matches:
            raise AutonomousControlError("enabled_profile_not_found",
                "The resolved account has no enabled execution profile.")
        if len(matches) > 1:
            raise AutonomousControlError("multiple_enabled_profiles",
                "Multiple enabled profiles match this account; provide profile_ref.")
        profile = matches[0]
        if not profile.get("strategy_template_id") or not profile.get("strategy_name") or not profile.get("strategy_version"):
            raise AutonomousControlError("strategy_not_configured", "The profile strategy is not configured.")
        risk = profile.get("risk")
        if not isinstance(risk, dict) or not REQUIRED_RISK_FIELDS.issubset(risk):
            raise AutonomousControlError("risk_limits_not_configured", "The profile risk limits are incomplete.")
        return profile

    @staticmethod
    def _decision_engine() -> dict[str, Any]:
        provider = (settings.autonomous_decision_provider or "").strip().lower()
        model = (settings.autonomous_decision_model or "").strip()
        missing: list[str] = []
        if provider != "openai": missing.append("AUTONOMOUS_DECISION_PROVIDER")
        if not model: missing.append("AUTONOMOUS_DECISION_MODEL")
        if not (settings.openai_api_key or "").strip(): missing.append("OPENAI_API_KEY")
        readiness = decision_provider_readiness(provider, model)
        if "provider_unavailable" in readiness["blocking_reasons"] and not missing:
            raise AutonomousControlError("decision_engine_not_configured",
                "The configured decision provider is unavailable.", reasons=["provider_unavailable"],
                details={"missing_settings": [], "decision_engine": {
                    "provider": provider or None, "model": model or None, "ready": False}})
        if missing:
            raise AutonomousControlError("decision_engine_not_configured",
                "Server-side autonomous decision configuration is incomplete.",
                reasons=["decision_engine_not_configured"], details={"missing_settings": missing,
                    "decision_engine": {"provider": provider or None, "model": model or None, "ready": False}})
        return {"provider": provider, "model": model, "ready": True}

    def _ensure_schedule(self, user_sub: str, profile: dict[str, Any]) -> dict[str, Any]:
        existing = self.schedules.get_profile_schedule(user_sub, profile["public_id"])
        created = existing is None
        default_times = strategy_default_times(profile)
        if existing and existing.get("enabled"):
            schedule = self.schedule_service._present(existing)
        elif existing:
            schedule = self.schedule_service.set_enabled(user_sub, existing["id"], True)
        else:
            schedule = self.schedule_service.save(user_sub, profile["public_id"], timezone_name="UTC",
                local_times=default_times, enabled=True)
        persisted = self.schedules.get_profile_schedule(user_sub, profile["public_id"])
        if not persisted or not persisted.get("enabled") or not persisted.get("next_run_at"):
            raise AutonomousControlError("schedule_persistence_failed", "The autonomous schedule was not persisted.")
        strategy_default = created or (persisted.get("timezone") == "UTC"
            and persisted.get("expression", {}).get("times") == default_times)
        return {"schedule_id": schedule["id"], "created": created, "enabled": True,
            "cadence": "strategy_default" if strategy_default else "existing",
            "next_run": schedule.get("next_run_at")}

    async def start(self, user_sub: str, environment: Environment, *, account_alias: str | None = None,
                    profile_ref: str | None = None, run_now: bool = True,
                    use_strategy_schedule: bool = True, live_confirmation: str | None = None) -> dict[str, Any]:
        if environment == "live":
            if live_confirmation != "ENABLE LIVE AUTONOMY":
                raise AutonomousControlError("live_confirmation_required",
                    "Type ENABLE LIVE AUTONOMY to enable live autonomous trading.")
            if not self.execution.get_autonomous_controls(user_sub)["live_execution_supported"]:
                raise AutonomousControlError("live_autonomous_execution_not_implemented",
                    "An audited live autonomous execution path is not implemented.")
        engine = self._decision_engine()
        account = self._account(user_sub, environment, account_alias)
        profile = self._profile(user_sub, account, profile_ref)
        worker = self.schedules.worker_health()
        if use_strategy_schedule and worker["status"] != "healthy":
            raise AutonomousControlError("autonomous_worker_unavailable",
                "The persistent autonomous worker is not healthy.")
        if not self.brokers.update_profile(user_sub, profile["public_id"],
                decision_provider=engine["provider"], model_identifier=engine["model"],
                minimum_confidence=settings.autonomous_default_minimum_confidence):
            raise AutonomousControlError("profile_configuration_persistence_failed",
                "The server-side decision configuration was not persisted.")
        schedule = self._ensure_schedule(user_sub, profile) if use_strategy_schedule else {
            "schedule_id": None, "created": False, "enabled": False,
            "cadence": None, "next_run": None}
        changes = {f"{environment}_autonomous_enabled": True}
        if environment == "demo": changes["global_autonomous_kill_switch"] = False
        controls = self.execution.update_autonomous_controls(user_sub, changes,
            updated_by=user_sub, source="assistant", reason=f"Desk started {environment} autonomous trading",
            audit_unchanged=True)
        if not controls[f"{environment}_autonomous_enabled"] or (environment == "demo" and controls["global_autonomous_kill_switch"]):
            raise AutonomousControlError("autonomous_control_persistence_failed",
                "The autonomous environment controls were not persisted.")
        immediate = None
        if run_now:
            seed = f"{user_sub}:{profile['public_id']}:{environment}:{controls['updated_at']}"
            run_key = f"desk-start:{hashlib.sha256(seed.encode()).hexdigest()[:32]}"
            result = await dispatch_autonomous_cycle(self.runner_factory(), user_sub, profile["public_id"], run_key,
                "desk:start_autonomous_trading")
            immediate = {"run_id": result.get("run_id"), "status": result.get("status"),
                "blocking_reasons": result.get("reason_codes") or [], "duplicate": bool(result.get("duplicate"))}
        blocked_run = bool(immediate and immediate["status"] in {"blocked", "skipped", "error"})
        return {"status": "started_with_blocked_run" if blocked_run else "started",
            "environment": environment, "account_alias": account["account_alias"],
            "profile_ref": profile["public_id"], "autonomous_enabled": controls[f"{environment}_autonomous_enabled"],
            "global_kill_switch": controls["global_autonomous_kill_switch"],
            "decision_engine": engine, "schedule": schedule, "immediate_run": immediate,
            "worker": worker}

    def stop(self, user_sub: str, environment: Environment, *, account_alias: str | None = None) -> dict[str, Any]:
        account = self._account(user_sub, environment, account_alias)
        controls = self.execution.update_autonomous_controls(user_sub,
            {f"{environment}_autonomous_enabled": False}, updated_by=user_sub, source="assistant",
            reason=f"Desk stopped {environment} autonomous trading", audit_unchanged=True)
        return {"status": "stopped", "environment": environment,
            "account_alias": account["account_alias"], "autonomous_enabled": False,
            "global_kill_switch": controls["global_autonomous_kill_switch"],
            "demo_autonomous_enabled": controls["demo_autonomous_enabled"],
            "live_autonomous_enabled": controls["live_autonomous_enabled"],
            "orders_and_positions_preserved": True}

    def emergency_stop(self, user_sub: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise AutonomousControlError("reason_required", "A reason is required for an emergency stop.")
        controls = self.execution.update_autonomous_controls(user_sub,
            {"global_autonomous_kill_switch": True}, updated_by=user_sub, source="assistant",
            reason=reason.strip(), audit_unchanged=True)
        return {"status": "stopped", "global_kill_switch": controls["global_autonomous_kill_switch"],
            "demo_autonomous_enabled": controls["demo_autonomous_enabled"],
            "live_autonomous_enabled": controls["live_autonomous_enabled"],
            "effective": controls["effective"], "orders_and_positions_preserved": True}

    def status(self, user_sub: str) -> dict[str, Any]:
        controls = self.execution.get_autonomous_controls(user_sub)
        accounts = self.brokers.list_accounts(user_sub)
        profiles = self.brokers.list_profiles(user_sub)
        schedules = self.schedule_service.list(user_sub)
        latest = self.execution.get_decision_run(user_sub)
        provider = (settings.autonomous_decision_provider or "").strip().lower()
        model = (settings.autonomous_decision_model or "").strip()
        readiness = decision_provider_readiness(provider, model)
        engine_ready = provider == "openai" and readiness["ready"]
        worker_health = self.schedules.worker_health()
        profile_rows = []
        for profile in profiles:
            if not profile.get("enabled"): continue
            reasons = AutonomousDecisionRunner(brokers=self.brokers, execution=self.execution)._blocking_reasons(
                profile, datetime.now(timezone.utc), user_sub)
            profile_rows.append({"profile_ref": profile["public_id"], "name": profile["name"],
                "account_alias": profile["account_alias"], "environment": profile["account_environment"],
                "blocking_reasons": reasons})
        blockers = ([] if engine_ready else (readiness["blocking_reasons"] or ["decision_engine_not_configured"]))
        if worker_health["status"] != "healthy": blockers.append("autonomous_worker_unavailable")
        blockers.extend(reason for profile in profile_rows for reason in profile["blocking_reasons"])
        return {"status": "ok", "global_kill_switch": controls["global_autonomous_kill_switch"],
            "demo_autonomous_enabled": controls["demo_autonomous_enabled"],
            "live_autonomous_enabled": controls["live_autonomous_enabled"],
            "selected_accounts": [{"account_alias": item["account_alias"], "environment": item["environment"]}
                for item in accounts if item.get("is_default_analysis")],
            "enabled_profiles": profile_rows,
            "decision_engine": {"provider": provider or None, "model": model or None,
                "ready": engine_ready, "status": readiness["status"] if provider == "openai" else "not_configured",
                "blocking_reasons": readiness["blocking_reasons"] if provider == "openai" else ["decision_engine_not_configured"]},
            "worker_health": worker_health, "schedules": schedules,
            "latest_run": AutonomousDecisionRunner._public_run(latest) if latest else None,
            "blocking_reasons": list(dict.fromkeys(blockers))}
