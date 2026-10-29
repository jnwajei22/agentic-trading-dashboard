# Kraken chart source

The Markets chart and price Overview use **Kraken only** for explicitly supported instruments. No broker or TradingView widget source selector is shown. Unsupported instruments show an explicit unavailable state. Broker account selection, order calculation, approvals, and execution continue to use the existing engine contracts.

## Data path

- History: browser → `/api/markets/kraken` → Kraken public REST OHLC. The route validates an instrument allowlist and 1/5/15/60-minute intervals, has a bounded upstream timeout, and returns no-store responses.
- Live: browser → `wss://ws.kraken.com/v2`, with public `ohlc`, `trade`, and `ticker` subscriptions. No account, API key, new database, trading worker, or persistent server process is needed. The same connection supplies Overview with 24-hour statistics and bid/ask; publication to the React Overview is coalesced to 250ms while trade/candle processing remains event-driven.
- Rendering: TradingView Lightweight Charts, shipped as an npm dependency. This is a chart renderer, not the TradingView hosted widget or its market-data feed. The built-in attribution remains enabled.

Kraken's OHLC channel publishes complete candle updates on trade events. The trade channel supplies individual matched trades and IDs; initial trade snapshots are disabled so the UI counter measures live events. The chart uses Kraken history and Kraken streaming candles together, rather than combining broker history with another venue's ticks. Quote-book changes do not manufacture candle trades.

On connection/reconnection the chart reloads history while consuming socket messages. For a given candle, a smaller trade count cannot overwrite a newer candle, preventing late REST responses from rewinding streamed prices. Missing intervals are not filled with invented trades. History is bounded to 500 bars and trade deduplication to 4096 IDs. A quiet market is not treated as a dead connection: the watchdog tracks transport messages/heartbeats separately from trades. Disconnections use bounded exponential retry; hidden tabs pause and resume with a fresh subscription and history repair.

The collapsed Feed details section exposes the last source trade ID/timestamp and candle update count. Matching DOM data attributes permit read-only deployed verification without exposing credentials or internal application state.

## Limits

These are **Kraken exchange trades**, not the connected broker's OTC forex quotes. Spreads, liquidity, prices, and activity can differ substantially. This source must not be used to calculate executable broker orders. A connected socket does not prove new trades occurred, and a short test does not prove lossless delivery. Unsupported instruments, including NZD/USD and gold, show unavailable pricing; there is no silent symbol substitution.

Public API access requires no paid subscription. No claim of unrestricted data redistribution is made; check provider terms before expanding the shared personal dashboard into a public commercial data product.

## Research feeds

The existing Finnhub news/calendar integration remains available when configured. On provider failure, News falls back to official BLS and ECB RSS announcements. Calendar falls back to the official BLS and BEA iCalendar release schedules, preserving available sources when one feed cannot be reached. These keyless sources are cached for five minutes with bounded response sizes and request timeouts, and carry visible coverage labels. Official calendar coverage is U.S. releases only; it supplies no consensus forecasts, actual results, or impact ratings. Time conversion respects Eastern daylight saving transitions. FRED context remains optional and separately attributed. HTML is rendered as plain text, DTD/entity declarations are rejected, and news links require HTTPS.

These fallbacks serve dashboard research only. They do not replace the autonomous engine's required-provider checks or clear execution news blackouts.

References:
- https://docs.kraken.com/api/docs/websocket-v2/ohlc/
- https://docs.kraken.com/api/docs/websocket-v2/trade/
- https://docs.kraken.com/api/docs/rest-api/get-ohlc-data/
- https://github.com/tradingview/lightweight-charts
