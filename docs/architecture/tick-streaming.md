# Tick streaming: implementation and activation boundary

Status: aggregation core implemented; upstream price subscription NOT activated.
The production broker chart still uses sampled REST data delivered over SSE.

## Implemented

`server/app/services/market_data/tick_bars.py` aggregates provider price events into
UTC-aligned candles. Each subscription binds source, instrument, interval and
explicit BID/ASK/TRADE basis. It handles current-bar out-of-order events,
deduplication, rollover, disconnect coverage and invalid prices. It neither
fabricates traded volume nor fills periods without ticks. The first bar and bars
spanning disconnects are flagged partial. It has no broker or order permissions.

Tests: `cd server && python -m pytest tests/unit/test_tick_bars.py -q`.
Test fixtures are synthetic unit-test inputs, not evidence of a working live feed.

## Confirmed activation blockers

- The configured TradeLocker integration uses its public REST API.
- Vercel environment inspection found no streaming-provider API credential.
- TradeLocker's published Streams API lists account/position/order events, not
  a documented price subscription. An account-events socket is not a tick feed.
- No alternate data vendor, new subscription or new cost has been authorized.

## Information required from HeroFX / TradeLocker

Request supported, read-only streaming bid/ask market data for the connected
HeroFX TradeLocker demo account and the dashboard's Forex Majors watchlist.
Ask for:

1. A documented price subscription endpoint and event schema, including source
   timestamps, symbol IDs, bid/ask fields, sequences and reconnect semantics.
2. Required credentials/entitlements and whether demo accounts are supported.
3. Connection/subscription limits, display permissions and any fees.
4. The candle price basis (BID, ASK or TRADE) matching broker history.
5. Historical backfill rules for gaps and market session boundaries.

The Developer Program is an inquiry path, not proof of price-feed entitlement:
https://tradelocker.typeform.com/devprogram

Sources:
https://public-api.tradelocker.com/docs/getting-started
https://public-api.tradelocker.com/reference/getquotes
https://public-api.tradelocker.com/reference/getinstrumentdetails
https://api-dev.tradelocker.com/streams-api/socket/docs/index.html

## Remaining integration work after supported access is available

Implement the provider-specific authenticated subscription and validate incoming
events. Keep credentials server-only. Feed events to the builder and reuse the
browser streaming transport. Backfill on reconnect before marking coverage
complete. Never overwrite broker history with external-vendor or midpoint bars.
Verify provider ticks, rendered current OHLC and rollover on the hosted deployment;
test disconnect recovery and symbol/timeframe changes. Preserve account ownership,
kill switches and all existing execution restrictions. No orders or trading
workers are needed for this verification.
