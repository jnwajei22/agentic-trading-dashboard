from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionMode(StrEnum):
    READ_ONLY = "read_only"
    DEMO_MANUAL = "demo_manual"
    DEMO_AUTONOMOUS = "demo_autonomous"


class ExecutionSettingsUpdate(StrictInputModel):
    execution_mode: ExecutionMode
    profile_ref: str = Field(min_length=1, max_length=80)


class AutonomousOrderProposal(StrictInputModel):
    snapshot_id: str = Field(min_length=1, max_length=80)
    pair: str = Field(min_length=6, max_length=10)
    side: Literal["long", "short"]
    order_type: Literal["market", "limit"] = "market"
    entry: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("reason_codes")
    @classmethod
    def safe_reason_codes(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            cleaned = value.strip().lower()
            if not cleaned or len(cleaned) > 64 or not all(c.isalnum() or c in "_-" for c in cleaned):
                raise ValueError("Reason codes must be short identifiers.")
            result.append(cleaned)
        return result


class AutonomousSubmissionRequest(StrictInputModel):
    preview_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AutonomousNoTradeRequest(StrictInputModel):
    snapshot_id: str = Field(min_length=1, max_length=80)
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    pairs_evaluated: list[str] = Field(default_factory=list, max_length=5)
