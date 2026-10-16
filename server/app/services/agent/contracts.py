from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field

class Mode(StrEnum):
    RESEARCH = 'research'
    COOP = 'co_op'
    AUTONOMOUS = 'autonomous'

class Context(BaseModel):
    model_config = ConfigDict(extra='forbid')
    market: str | None = Field(default=None, max_length=120)
    account_ref: str | None = Field(default=None, max_length=100)
    profile_ref: str | None = Field(default=None, max_length=100)
    schedule_id: str | None = Field(default=None, max_length=100)
    preview_id: str | None = Field(default=None, max_length=100)
    run_id: str | None = Field(default=None, max_length=100)
    page: str | None = Field(default=None, max_length=200)

class TurnRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=8000)
    mode: Mode = Mode.RESEARCH
    context: Context = Field(default_factory=Context)
