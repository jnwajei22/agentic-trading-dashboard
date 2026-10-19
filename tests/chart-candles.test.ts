import test from "node:test"
import assert from "node:assert/strict"
import { mergeChartCandles } from "../lib/afd/chart-candles"
const bar = (timestamp: number, close = 1) => ({ timestamp, open: 1, high: Math.max(1, close), low: Math.min(1, close), close })

test("a fresh broker tail updates the visible forming candle and preserves history", () => {
  const old = [bar(1000), bar(2000)]
  const next = mergeChartCandles(old, [bar(2000, 1.2)])
  assert.deepEqual(next, [bar(1000), bar(2000, 1.2)])
  assert.equal(old[1].close, 1)
})
test("new candle boundaries append without duplicates or a manual refresh", () => {
  assert.deepEqual(mergeChartCandles([bar(1000),bar(2000)], [bar(3000),bar(2000, 1.2)]), [bar(1000),bar(2000,1.2),bar(3000)])
})
test("missing tail preserves existing bars and rolling history stays bounded", () => {
  assert.deepEqual(mergeChartCandles([bar(1000)], []), [bar(1000)])
  const merged = mergeChartCandles(Array.from({length:120},(_,i)=>bar(i)),[bar(120)])
  assert.equal(merged.length,120)
  assert.equal(merged[0].timestamp,1)
  assert.equal(merged.at(-1)?.timestamp,120)
})
