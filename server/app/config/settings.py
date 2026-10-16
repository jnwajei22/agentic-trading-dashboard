import os
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = Field(default_factory=lambda: "production" if os.getenv("VERCEL") == "1" else "development")
    app_timezone: str = "America/Chicago"
    public_base_url: str = "https://mcp.justinnwajei.com"
    frontend_origin: str = "https://app.agenticforexdesk.com"
    sqlite_path: str = "storage/app.db"
    broker_secret_key: str | None = None
    broker_secret_keys_json: str = "{}"
    active_encryption_key_version: str = "v1"
    allow_env_broker_fallback: bool = False

    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    supabase_db_url: str | None = Field(default=None, repr=False)
    database_url: str | None = None
    tradingview_webhook_secret: str | None = None
    workspace_owner_sub: str = Field(default="shared-workspace", min_length=1, pattern=r".*\S.*")

    live_trading_enabled: bool = False
    kill_switch_enabled: bool = True
    market_data_provider: str = "mock"

    default_max_risk_percent: float = 0.5
    default_max_daily_loss_percent: float = 2.0
    default_max_weekly_loss_percent: float = 5.0
    default_min_reward_risk: float = 1.5

    tradelocker_environment: str = "demo"
    tradelocker_base_url: str = "https://demo.tradelocker.com/backend-api"
    tradelocker_demo_base_url: str = "https://demo.tradelocker.com/backend-api"
    tradelocker_username: str | None = None
    tradelocker_password: str | None = None
    tradelocker_server: str | None = None
    tradelocker_account_id: str | None = None
    tradelocker_account_number: str | None = None
    tradelocker_config_cache_ttl_seconds: int = 900
    tradelocker_candle_cache_grace_seconds: int = Field(default=30, ge=0, le=300)
    tradelocker_candle_cache_max_stale_seconds: int = Field(default=86400, ge=0)
    tradelocker_candle_cache_max_entries: int = Field(default=1000, ge=10)
    tradelocker_rate_limit_max_retries: int = Field(default=2, ge=0, le=5)
    tradelocker_rate_limit_max_backoff_seconds: float = Field(default=8.0, ge=0.1, le=60)
    tradelocker_rate_limit_min_interval_seconds: float = Field(default=0.35, ge=0, le=10)

    autonomous_snapshot_ttl_seconds: int = 60
    autonomous_preview_ttl_seconds: int = 60
    autonomous_quote_max_age_seconds: int = 30
    autonomous_price_tolerance_percent: float = 0.25
    autonomous_max_spread_pips: float = 3.0
    autonomous_broker_verification_max_attempts: int = Field(default=6, ge=1, le=20)
    autonomous_broker_verification_timeout_seconds: float = Field(default=12.0, ge=1, le=30)
    autonomous_broker_verification_initial_delay_seconds: float = Field(default=0.75, ge=0, le=5)
    autonomous_broker_verification_max_delay_seconds: float = Field(default=2.0, ge=0, le=10)
    autonomous_news_blackout_minutes: int = 30
    autonomous_decision_provider: str = "no_trade"
    autonomous_decision_model: str = "gpt-5.6"
    autonomous_decision_timeout_seconds: float = 30
    autonomous_decision_max_retries: int = 1
    autonomous_decision_max_input_chars: int = 80000
    autonomous_default_minimum_confidence: float = 0.70
    autonomous_maximum_instruments_screened: int = Field(default=100,ge=1,le=1000)
    autonomous_maximum_instruments_deeply_analyzed: int = Field(default=5,ge=1,le=50)
    autonomous_maximum_upstream_requests_per_run: int = Field(default=40,ge=1,le=500)
    autonomous_maximum_run_duration_seconds: float = Field(default=45,gt=0,le=300)
    autonomous_max_arming_hours: int = 24
    agent_model: str | None = None
    openai_api_key: str | None = Field(default=None, repr=False)
    autonomous_scheduler_poll_seconds: float = Field(default=30,gt=0)
    autonomous_scheduler_lease_seconds: int = Field(default=180,ge=30)
    autonomous_scheduler_batch_size: int = Field(default=20,ge=1,le=100)
    autonomous_scheduler_max_retries: int = Field(default=2,ge=0,le=5)
    autonomous_scheduler_retry_base_seconds: int = Field(default=30,ge=1)
    autonomous_scheduler_retry_cap_seconds: int = Field(default=300,ge=1)
    autonomous_scheduler_heartbeat_stale_seconds: int = Field(default=120,ge=30)
    autonomous_scheduler_required_for_readiness: bool = False
    autonomous_scheduler_embedded: bool = False

    finnhub_enabled: bool = False
    finnhub_api_key: str | None = None
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    finnhub_timeout_seconds: float = 15
    finnhub_max_retries: int = 2
    finnhub_cache_ttl_seconds: int = 300

    fred_enabled: bool = False
    fred_api_key: str | None = None
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    fred_timeout_seconds: float = 15
    fred_max_retries: int = 2
    fred_cache_ttl_seconds: int = 3600
    macro_catalog_json: str = "{}"

    market_data_default_candles: int = 300
    market_data_max_response_candles: int = 2000
    market_data_max_retrieval_candles: int = 10000
    market_data_max_pages: int = 50
    market_series_cache_ttl_seconds: int = 600
    market_series_cache_max_items: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
