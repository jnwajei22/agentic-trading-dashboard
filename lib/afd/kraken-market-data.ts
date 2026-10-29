/** Public market data only. These symbols never identify an execution account. */
const SYMBOLS: Record<string, string> = {
  "forex:EUR/USD": "EUR/USD", "forex:GBP/USD": "GBP/USD",
  "forex:AUD/USD": "AUD/USD", "forex:USD/CAD": "USD/CAD",
  "forex:USD/CHF": "USD/CHF", "forex:USD/JPY": "USD/JPY",
  "crypto:BTC/USD": "BTC/USD", "crypto:ETH/USD": "ETH/USD",
}
export const KRAKEN_INTERVALS = [1, 5, 15, 60] as const
export const krakenSymbol = (instrument: string) => SYMBOLS[instrument] ?? null
export type KrakenCandle = { time: number; open: number; high: number; low: number; close: number; trades: number }
export type KrakenTrade = { symbol: string; trade_id: number; timestamp: string; price: number }
export type KrakenTicker = { symbol: string; last: number; bid: number; ask: number; high: number; low: number; change: number; changePercent: number; volume: number; vwap: number; checkedAt: string; tradeTime: string | null }

export function parseKrakenTicker(row: Record<string, unknown>, symbol: string): KrakenTicker | null {
  const keys = ["last", "bid", "ask", "high", "low", "change", "change_pct", "volume", "vwap"]
  if (row.symbol !== symbol || keys.some(key => typeof row[key] !== "number" || !Number.isFinite(row[key]))) return null
  const n = row as Record<string, number>
  if (n.last <= 0 || n.bid <= 0 || n.ask < n.bid || n.high < n.low || n.volume < 0) return null
  return { symbol, last: n.last, bid: n.bid, ask: n.ask, high: n.high, low: n.low, change: n.change, changePercent: n.change_pct, volume: n.volume, vwap: n.vwap, checkedAt: new Date().toISOString(), tradeTime: typeof row.timestamp === "string" ? row.timestamp : null }
}
const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0

function candle(time: number, open: unknown, high: unknown, low: unknown, close: unknown, trades: unknown): KrakenCandle | null {
  if (!Number.isInteger(time) || time <= 0 || !positive(open) || !positive(high) || !positive(low) || !positive(close)
    || high < Math.max(open, close) || low > Math.min(open, close) || low > high
    || typeof trades !== "number" || !Number.isInteger(trades) || trades < 0) return null
  return { time, open, high, low, close, trades }
}

export function parseKrakenHistory(payload: unknown): KrakenCandle[] {
  const data = payload as { error?: unknown[]; result?: Record<string, unknown> }
  if (!data || !Array.isArray(data.error) || data.error.length || !data.result) throw new Error("Kraken history unavailable")
  const rows = Object.entries(data.result).find(([key, value]) => key !== "last" && Array.isArray(value))?.[1]
  if (!Array.isArray(rows)) throw new Error("Invalid Kraken history")
  return rows.map(row => Array.isArray(row) ? candle(Number(row[0]), Number(row[1]), Number(row[2]), Number(row[3]), Number(row[4]), row[7]) : null)
    .filter((row): row is KrakenCandle => row !== null).sort((a, b) => a.time - b.time).slice(-500)
}

export function parseKrakenCandles(payload: unknown, symbol: string, interval: number): KrakenCandle[] {
  const message = payload as { channel?: string; type?: string; data?: Record<string, unknown>[] }
  if (message?.channel !== "ohlc" || !["snapshot", "update"].includes(message.type ?? "") || !Array.isArray(message.data)) return []
  return message.data.filter(row => row.symbol === symbol && row.interval === interval).map(row =>
    candle(Date.parse(String(row.interval_begin)) / 1000, row.open, row.high, row.low, row.close, row.trades)
  ).filter((row): row is KrakenCandle => row !== null)
}

export function parseKrakenTrades(payload: unknown, symbol: string): KrakenTrade[] {
  const message = payload as { channel?: string; type?: string; data?: KrakenTrade[] }
  if (message?.channel !== "trade" || message.type !== "update" || !Array.isArray(message.data)) return []
  return message.data.filter(row => row.symbol === symbol && Number.isSafeInteger(row.trade_id) && positive(row.price) && Number.isFinite(Date.parse(row.timestamp)))
}

/** Trade counts prevent a slow REST snapshot from overwriting newer socket data. */
export function mergeKrakenCandles(previous: KrakenCandle[], incoming: KrakenCandle[]): KrakenCandle[] {
  const bars = new Map(previous.map(row => [row.time, row]))
  for (const row of incoming) {
    const current = bars.get(row.time)
    if (!current || row.trades >= current.trades) bars.set(row.time, row)
  }
  return [...bars.values()].sort((a, b) => a.time - b.time).slice(-500)
}
