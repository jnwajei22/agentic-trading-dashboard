import test from "node:test"
import assert from "node:assert/strict"
import { krakenSymbol, mergeKrakenCandles, parseKrakenCandles, parseKrakenHistory, parseKrakenTrades } from "../lib/afd/kraken-market-data"

const bar = (time: number, close = 1.12, trades = 1) => ({ time, open: 1.12, high: Math.max(1.12, close), low: Math.min(1.12, close), close, trades })
test("unsupported forex and CFDs never silently map to a different instrument", () => {
  assert.equal(krakenSymbol("forex:EUR/USD"), "EUR/USD")
  assert.equal(krakenSymbol("forex:NZD/USD"), null)
  assert.equal(krakenSymbol("metals:XAU/USD"), null)
})
test("late REST history cannot rewind a forming candle already updated by trades", () => {
  const live = bar(300, 1.13, 15)
  assert.deepEqual(mergeKrakenCandles([live], [bar(300, 1.125, 12)]), [live])
  assert.equal(mergeKrakenCandles([bar(300)], [live]).at(-1)?.close, 1.13)
})
test("rollovers append sorted unique candles without manufacturing missing intervals", () => {
  const merged = mergeKrakenCandles([bar(300)], [bar(900), bar(300, 1.13, 2)])
  assert.deepEqual(merged.map(row => row.time), [300, 900])
  assert.equal(merged[0].close, 1.13)
  assert.equal(mergeKrakenCandles([], Array.from({ length: 600 }, (_, i) => bar(i + 1))).length, 500)
})
test("trade batches retain individual IDs; snapshots and other instruments are not live events", () => {
  const trade = { symbol: "EUR/USD", trade_id: 123, timestamp: "2026-10-23T03:00:00.000001Z", price: 1.12 }
  const payload = { channel: "trade", type: "update", data: [trade, { ...trade, trade_id: 124 }, { ...trade, symbol: "GBP/USD" }] }
  assert.deepEqual(parseKrakenTrades(payload, "EUR/USD").map(row => row.trade_id), [123,124])
  assert.deepEqual(parseKrakenTrades({ ...payload, type: "snapshot" }, "EUR/USD"), [])
})
test("stream parsing validates interval, instrument, timestamp and OHLC invariants", () => {
  const row = { symbol: "EUR/USD", interval: 5, interval_begin: "2026-10-23T03:00:00Z", ...bar(1) }
  const payload = { channel: "ohlc", type: "update", data: [row] }
  assert.equal(parseKrakenCandles(payload, "EUR/USD", 5).length, 1)
  assert.deepEqual(parseKrakenCandles(payload, "EUR/USD", 1), [])
  assert.deepEqual(parseKrakenCandles(payload, "GBP/USD", 5), [])
  assert.deepEqual(parseKrakenCandles({ ...payload, data: [{ ...row, high: 1 }] }, "EUR/USD", 5), [])
})
test("REST history parses Kraken trade candles and rejects API errors", () => {
  const rows = parseKrakenHistory({ error: [], result: { ZEUREUR: [[300, "1.12", "1.13", "1.11", "1.125", "1.12", "10", 5]], last: 300 } })
  assert.equal(rows[0].close, 1.125)
  assert.equal(rows[0].trades, 5)
  assert.throws(() => parseKrakenHistory({ error: ["EGeneral:Too many requests"] }))
})
