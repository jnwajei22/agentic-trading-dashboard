from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradingPolicy(StrictModel):
    mode: Literal["adaptive", "preset"] = "adaptive"
    preset_id: Literal["hourly_forex"] | None = None
    decision_interval: Literal["scheduled"] = "scheduled"
    minimum_confidence: float = Field(0.70, ge=0, le=1)

    @model_validator(mode="after")
    def preset_required(self) -> "TradingPolicy":
        if self.mode == "preset" and not self.preset_id:
            raise ValueError("preset_id is required in preset mode")
        return self


class MarketUniverse(StrictModel):
    mode: Literal["all_available", "groups", "custom"] = "all_available"
    groups: list[str] = Field(default_factory=list)
    included_instrument_ids: list[str] = Field(default_factory=list)
    excluded_instrument_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def selection_required(self) -> "MarketUniverse":
        if self.mode == "groups" and not self.groups:
            raise ValueError("groups must not be empty in groups mode")
        if self.mode == "custom" and not self.included_instrument_ids:
            raise ValueError("included_instrument_ids must not be empty in custom mode")
        return self


class RiskPolicy(StrictModel):
    mode: Literal["fixed", "adaptive"] = "fixed"
    fixed_risk_pct: float = Field(0.25, gt=0, le=1)
    base_risk_pct: float = Field(0.25, gt=0, le=1)
    minimum_risk_pct: float = Field(0.10, gt=0, le=1)
    maximum_risk_pct: float = Field(0.50, gt=0, le=1)
    maximum_total_open_risk_pct: float = Field(1.0, gt=0, le=10)
    maximum_margin_utilization_pct: float | None = Field(None, gt=0, le=100)
    maximum_correlated_risk_pct: float | None = Field(None, gt=0, le=100)
    daily_loss_limit_pct: float = Field(3.0, gt=0, le=3)
    drawdown_cutoff_pct: float = Field(10.0, gt=0, le=10)
    maximum_open_positions: int = Field(3, ge=1, le=100)
    maximum_pending_entry_orders: int = Field(1, ge=0, le=100)
    maximum_new_entries_per_day: int = Field(2, ge=1, le=100)

    @model_validator(mode="after")
    def adaptive_range(self) -> "RiskPolicy":
        if not self.minimum_risk_pct <= self.base_risk_pct <= self.maximum_risk_pct:
            raise ValueError("minimum_risk_pct <= base_risk_pct <= maximum_risk_pct is required")
        return self


class CapitalAllocation(StrictModel):
    mode: Literal["full_account", "fixed_amount", "equity_percentage"] = "full_account"
    fixed_amount: float | None = Field(None, gt=0)
    equity_percentage: float | None = Field(None, gt=0, le=100)
    risk_base: Literal["account_equity", "allocated_capital"] = "account_equity"
    compounding_mode: Literal["disabled", "realized_pnl", "periodic_rebalance"] = "disabled"
    maximum_margin_utilization_pct: float = Field(70.0, gt=0, le=100)
    maximum_gross_exposure_multiple: float | None = Field(None, gt=0)
    allow_shared_capital: bool = False

    @model_validator(mode="after")
    def allocation_value_required(self) -> "CapitalAllocation":
        if self.mode == "fixed_amount" and self.fixed_amount is None:
            raise ValueError("fixed_amount is required in fixed_amount mode")
        if self.mode == "equity_percentage" and self.equity_percentage is None:
            raise ValueError("equity_percentage is required in equity_percentage mode")
        return self


class CapitalAllocationPatch(StrictModel):
    mode: Literal["full_account", "fixed_amount", "equity_percentage"] | None = None
    fixed_amount: float | None = Field(None, gt=0)
    equity_percentage: float | None = Field(None, gt=0, le=100)
    risk_base: Literal["account_equity", "allocated_capital"] | None = None
    compounding_mode: Literal["disabled", "realized_pnl", "periodic_rebalance"] | None = None
    maximum_margin_utilization_pct: float | None = Field(None, gt=0, le=100)
    maximum_gross_exposure_multiple: float | None = Field(None, gt=0)
    allow_shared_capital: bool | None = None


class StopLossPolicy(StrictModel):
    enabled: bool = True
    mode: Literal["adaptive_structure", "volatility", "fixed_distance", "fixed_percentage"] = "adaptive_structure"
    fixed_distance: float | None = Field(None, gt=0)
    fixed_percentage: float | None = Field(None, gt=0, le=100)
    maximum_risk_distance: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def fixed_value_required(self) -> "StopLossPolicy":
        if self.mode == "fixed_distance" and self.fixed_distance is None:
            raise ValueError("fixed_distance is required for fixed_distance mode")
        if self.mode == "fixed_percentage" and self.fixed_percentage is None:
            raise ValueError("fixed_percentage is required for fixed_percentage mode")
        return self


class TakeProfitPolicy(StrictModel):
    enabled: bool = True
    mode: Literal["reward_to_risk", "adaptive_structure", "trailing_only", "none"] = "reward_to_risk"
    minimum_reward_to_risk: float = Field(1.5, ge=0)
    target_reward_to_risk: float = Field(2.0, ge=0)

    @model_validator(mode="after")
    def reward_range(self) -> "TakeProfitPolicy":
        if self.target_reward_to_risk < self.minimum_reward_to_risk:
            raise ValueError("target_reward_to_risk must be at least minimum_reward_to_risk")
        return self


class TrailingStopPolicy(StrictModel):
    enabled: bool = False
    activation_mode: Literal["reward_multiple", "fixed_distance", "percentage"] = "reward_multiple"
    activation_value: float | None = Field(None, gt=0)
    trail_value: float | None = Field(None, gt=0)


class BreakEvenPolicy(StrictModel):
    enabled: bool = False
    activation_reward_multiple: float = Field(1.0, gt=0)
    offset: float = 0


class ExitPolicy(StrictModel):
    stop_loss: StopLossPolicy = Field(default_factory=StopLossPolicy)
    take_profit: TakeProfitPolicy = Field(default_factory=TakeProfitPolicy)
    trailing_stop: TrailingStopPolicy = Field(default_factory=TrailingStopPolicy)
    break_even: BreakEvenPolicy = Field(default_factory=BreakEvenPolicy)
    partial_exits: list[dict[str, Any]] = Field(default_factory=list)


class SchedulePolicy(StrictModel):
    timezone: str = "America/Chicago"
    times: list[str] = Field(default_factory=list)
    market_aware: bool = True
    run_when_any_selected_market_is_open: bool = True


class ForexExtension(StrictModel):
    lot_sizing: Literal["risk_based", "fixed"] = "risk_based"
    pip_distance_limit: float | None = Field(None, gt=0)
    maximum_currency_exposure_pct: float | None = Field(None, gt=0, le=100)
    sessions: list[str] = Field(default_factory=list)


class EquitiesExtension(StrictModel):
    sizing: Literal["shares", "fractional", "risk_based"] = "risk_based"
    extended_hours: bool = False
    maximum_sector_exposure_pct: float | None = Field(None, gt=0, le=100)
    restrict_earnings_window: bool = True


class OptionsExtension(StrictModel):
    minimum_days_to_expiration: int = Field(7, ge=0)
    maximum_days_to_expiration: int = Field(60, ge=1)
    strike_selection: Literal["delta", "moneyness", "fixed"] = "delta"
    maximum_contracts: int = Field(1, ge=1)
    maximum_premium: float = Field(gt=0)
    maximum_absolute_delta: float | None = Field(None, gt=0, le=1)
    assignment_acknowledged: bool = False

    @model_validator(mode="after")
    def expiration_range(self) -> "OptionsExtension":
        if self.maximum_days_to_expiration < self.minimum_days_to_expiration:
            raise ValueError("maximum_days_to_expiration must be at least minimum_days_to_expiration")
        return self


class ExecutionProfileV2(StrictModel):
    schema_version: Literal[2] = 2
    asset_class: Literal["forex", "equities", "options"] = "forex"
    trading_account: str | None = None
    trading_policy: TradingPolicy = Field(default_factory=TradingPolicy)
    market_universe: MarketUniverse = Field(default_factory=MarketUniverse)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    capital_allocation: CapitalAllocation = Field(default_factory=CapitalAllocation)
    exit_policy: ExitPolicy = Field(default_factory=ExitPolicy)
    schedule_policy: SchedulePolicy = Field(default_factory=SchedulePolicy)
    provider_capability_requirements: list[str] = Field(default_factory=list)
    forex: ForexExtension | None = Field(default_factory=ForexExtension)
    equities: EquitiesExtension | None = None
    options: OptionsExtension | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def extension_matches_asset_class(self) -> "ExecutionProfileV2":
        if self.asset_class == "equities" and self.equities is None:
            raise ValueError("equities extension is required for equities strategies")
        if self.asset_class == "options" and self.options is None:
            raise ValueError("options extension is required for options strategies")
        return self


def deep_merge(original: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(original)
    for key, value in patch.items():
        result[key] = deep_merge(result.get(key, {}), value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def migrate_legacy_profile(profile: dict[str, Any]) -> ExecutionProfileV2:
    risk = profile.get("risk") or {}
    allowed = profile.get("allowed_instruments") or []
    strategy = profile.get("strategy_name")
    legacy_risk = float(risk.get("risk_per_trade_percent", risk.get("risk_per_trade", 0.25)))
    data: dict[str, Any] = {
        "trading_policy": {
            "mode": "preset" if strategy == "hourly_forex" else "adaptive",
            "preset_id": "hourly_forex" if strategy == "hourly_forex" else None,
            "minimum_confidence": profile.get("minimum_confidence", 0.70),
        },
        "market_universe": {
            "mode": "custom" if allowed else "all_available",
            "included_instrument_ids": [str(item) for item in allowed],
        },
        "risk_policy": {
            "mode": "fixed",
            "fixed_risk_pct": legacy_risk,
            "base_risk_pct": legacy_risk,
            "minimum_risk_pct": min(0.10, legacy_risk),
            "maximum_risk_pct": max(0.50, legacy_risk),
            "daily_loss_limit_pct": risk.get("daily_loss_limit_percent", 3.0),
            "drawdown_cutoff_pct": risk.get("drawdown_cutoff_percent", 10.0),
            "maximum_open_positions": risk.get("maximum_open_positions", 3),
            "maximum_pending_entry_orders": risk.get("maximum_pending_orders", 1),
            "maximum_new_entries_per_day": risk.get("maximum_new_entries_per_day", 2),
        },
        "exit_policy": {"take_profit": {
            "mode": "reward_to_risk",
            "minimum_reward_to_risk": risk.get("minimum_reward_risk", risk.get("minimum_reward_to_risk", 1.5)),
            "target_reward_to_risk": max(2.0, risk.get("minimum_reward_risk", 1.5)),
        }},
        "enabled": bool(profile.get("enabled", True)),
    }
    return ExecutionProfileV2.model_validate(data)
