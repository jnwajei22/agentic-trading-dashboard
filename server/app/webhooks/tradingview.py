from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from app.config.secrets import EnvironmentSecretProvider
from app.models.webhooks import TradingViewSignal
from app.services.instruments import instrument_mapper
from app.services.watchlist import is_allowed_pair, normalize_pair
from app.storage.signal_intents import SignalIntentRepository

router = APIRouter()
MAX_PAYLOAD_BYTES = 32_768
MAX_SIGNAL_AGE = timedelta(minutes=5)
_requests: dict[str, deque[datetime]] = defaultdict(deque)


@router.post("/tradingview", status_code=202)
async def receive_tradingview_signal(request: Request,
    x_tradingview_secret: str | None = Header(default=None),
    x_tradingview_signature: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None)):
    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Signal payload is too large.")
    secret = EnvironmentSecretProvider().get("tradingview_webhook_secret")
    if not secret:
        raise HTTPException(status_code=503, detail="TradingView signal ingress is not configured.")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    valid = hmac.compare_digest(x_tradingview_secret or "", secret) or hmac.compare_digest(x_tradingview_signature or "", expected)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    client = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc)
    bucket = _requests[client]
    while bucket and bucket[0] < now - timedelta(minutes=1): bucket.popleft()
    if len(bucket) >= 60: raise HTTPException(status_code=429, detail="Signal rate limit exceeded.")
    bucket.append(now)
    try:
        raw = json.loads(body)
        payload = TradingViewSignal.model_validate(raw)
    except (json.JSONDecodeError, ValidationError):
        raise HTTPException(status_code=422, detail="Invalid TradingView signal payload.") from None
    timestamp = payload.timestamp.replace(tzinfo=payload.timestamp.tzinfo or timezone.utc).astimezone(timezone.utc)
    if abs(now - timestamp) > MAX_SIGNAL_AGE:
        raise HTTPException(status_code=422, detail="Signal timestamp is outside the accepted window.")
    pair = normalize_pair(payload.pair)
    if not is_allowed_pair(pair): raise HTTPException(status_code=400, detail="Pair is not allowed.")
    canonical_id = instrument_mapper.canonical_id("forex", pair)
    key = idempotency_key or hashlib.sha256(body).hexdigest()
    created = SignalIntentRepository().create(key, canonical_id, payload.model_dump(mode="json"))
    return {"status":"accepted" if created else "duplicate_ignored","provider_type":"tradingview_signal",
        "canonical_id":canonical_id,"idempotency_key":key,"can_place_trade":False,"order_submitted":False,
        "next_step":"backend_strategy_and_risk_validation"}
