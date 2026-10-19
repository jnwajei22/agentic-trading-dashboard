export type ChartCandle = { timestamp: number; open: number; high: number; low: number; close: number }

/** Broker updates replace a forming candle at the same timestamp, then append new bars. */
export function mergeChartCandles(previous: ChartCandle[], incoming: ChartCandle[]): ChartCandle[] {
  const candles = new Map(previous.map(candle => [candle.timestamp, candle]))
  for (const candle of incoming) candles.set(candle.timestamp, candle)
  return [...candles.values()].sort((a, b) => a.timestamp - b.timestamp).slice(-120)
}
