"use client"

import { useEffect, useRef, useState } from "react"
import { useTheme } from "next-themes"
import { CandlestickSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts"
import { Button } from "@/components/ui/button"
import { KRAKEN_INTERVALS, mergeKrakenCandles, parseKrakenCandles, parseKrakenTrades, parseKrakenTicker, type KrakenCandle, type KrakenTrade, type KrakenTicker } from "@/lib/afd/kraken-market-data"

type FeedState = { status: string; trades: number; candleUpdates: number; lastTrade: KrakenTrade | null; candle: KrakenCandle | null; historyError: boolean }
const INITIAL: FeedState = { status: "Connecting…", trades: 0, candleUpdates: 0, lastTrade: null, candle: null, historyError: false }

export function KrakenMarketChart({ instrument, symbol, label, onTicker }: { instrument: string; symbol: string; label: string; onTicker?: (ticker: KrakenTicker) => void }) {
  const container = useRef<HTMLDivElement>(null)
  const { resolvedTheme } = useTheme()
  const [interval, setIntervalMinutes] = useState(5)
  const [retry, setRetry] = useState(0)
  const [feed, setFeed] = useState<FeedState>(INITIAL)
  const precision = symbol.endsWith("JPY") ? 3 : symbol.startsWith("BTC") || symbol.startsWith("ETH") ? 2 : 5

  useEffect(() => {
    if (!container.current) return
    const light = resolvedTheme === "light"
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: light ? "#ffffff" : "#171b1e" }, textColor: light ? "#58636d" : "#9aa6b0", attributionLogo: true },
      grid: { vertLines: { color: "rgba(120,130,140,0.06)" }, horzLines: { color: "rgba(120,130,140,0.08)" } },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, { upColor: "#10b981", downColor: "#ef4444", borderVisible: false, wickUpColor: "#10b981", wickDownColor: "#ef4444", priceFormat: { type: "price", precision, minMove: 10 ** -precision } })
    const toChart = (bar: KrakenCandle) => ({ ...bar, time: bar.time as UTCTimestamp })
    let bars: KrakenCandle[] = []
    let socket: WebSocket | null = null
    let stopped = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let historyRequest: AbortController | null = null
    let lastMessage = Date.now()
    let backoff = 1000
    let fitted = false
    let historyFitted = false
    let tickerTimer: ReturnType<typeof setTimeout> | undefined
    let pendingTicker: KrakenTicker | null = null
    const seenTrades = new Set<number>()
    setFeed(INITIAL)

    function publishCandles(incoming: KrakenCandle[], history = false) {
      if (stopped || !incoming.length) return
      const previousLast = bars.at(-1)
      bars = mergeKrakenCandles(bars, incoming)
      if (history || !previousLast) series.setData(bars.map(toChart))
      else for (const bar of incoming.sort((a, b) => a.time - b.time)) {
        if (bar.time >= previousLast.time && bars.find(row => row.time === bar.time) === bar) series.update(toChart(bar))
      }
      if (!fitted || (history && !historyFitted)) { chart.timeScale().fitContent(); fitted = true; if (history) historyFitted = true }
      setFeed(current => ({ ...current, candle: bars.at(-1) ?? null }))
    }

    async function loadHistory(connection: WebSocket) {
      historyRequest?.abort()
      const controller = new AbortController()
      historyRequest = controller
      try {
        const query = new URLSearchParams({ instrument, interval: String(interval) })
        const response = await fetch(`/api/markets/kraken?${query}`, { cache: "no-store", signal: controller.signal })
        if (!response.ok) throw new Error("History unavailable")
        const data = await response.json() as { symbol: string; interval: number; candles: KrakenCandle[] }
        if (stopped || socket !== connection || controller.signal.aborted) return
        if (data.symbol !== symbol || data.interval !== interval || !Array.isArray(data.candles)) throw new Error("Invalid history")
        publishCandles(data.candles, true)
        setFeed(current => ({ ...current, historyError: false }))
      } catch {
        if (!stopped && !controller.signal.aborted) setFeed(current => ({ ...current, historyError: true }))
      }
    }

    function connect() {
      if (stopped || document.hidden) return
      clearTimeout(retryTimer)
      const connection = new WebSocket("wss://ws.kraken.com/v2")
      socket = connection
      lastMessage = Date.now()
      setFeed(current => ({ ...current, status: "Connecting…" }))
      const acknowledged = new Set<string>()
      connection.onopen = () => {
        connection.send(JSON.stringify({ method: "subscribe", params: { channel: "ohlc", symbol: [symbol], interval, snapshot: true } }))
        connection.send(JSON.stringify({ method: "subscribe", params: { channel: "trade", symbol: [symbol], snapshot: false } }))
        connection.send(JSON.stringify({ method: "subscribe", params: { channel: "ticker", symbol: [symbol], event_trigger: "bbo", snapshot: true } }))
        void loadHistory(connection)
      }
      connection.onmessage = event => {
        if (stopped || socket !== connection) return
        lastMessage = Date.now()
        try {
          const message = JSON.parse(event.data)
          if (message.channel === "ticker" && Array.isArray(message.data)) {
            for (const row of message.data) pendingTicker = parseKrakenTicker(row, symbol) ?? pendingTicker
            if (!tickerTimer) tickerTimer = setTimeout(() => { tickerTimer = undefined; if (!stopped && pendingTicker) onTicker?.(pendingTicker) }, 250)
          }
          if (message.method === "subscribe") {
            if (!message.success) { setFeed(current => ({ ...current, status: "Kraken rejected this subscription. Reconnect to retry." })); connection.onclose = null; connection.close(); return }
            acknowledged.add(message.result?.channel)
            if (acknowledged.has("ohlc") && acknowledged.has("trade")) { backoff = 1000; setFeed(current => ({ ...current, status: "Streaming" })) }
          }
          const candles = parseKrakenCandles(message, symbol, interval)
          publishCandles(candles)
          if (message.type === "update" && candles.length) setFeed(current => ({ ...current, candleUpdates: current.candleUpdates + candles.length }))
          const trades = parseKrakenTrades(message, symbol).filter(trade => {
            if (seenTrades.has(trade.trade_id)) return false
            seenTrades.add(trade.trade_id)
            if (seenTrades.size > 4096) seenTrades.delete(seenTrades.values().next().value!)
            return true
          })
          if (trades.length) setFeed(current => ({ ...current, trades: current.trades + trades.length, lastTrade: trades.reduce((last, next) => next.trade_id > last.trade_id ? next : last, current.lastTrade ?? trades[0]) }))
        } catch { setFeed(current => ({ ...current, status: "Invalid feed update. Reconnecting…" })); connection.close() }
      }
      connection.onerror = () => connection.close()
      connection.onclose = () => {
        if (stopped || socket !== connection) return
        historyRequest?.abort()
        setFeed(current => ({ ...current, status: "Feed paused. Reconnecting…" }))
        retryTimer = setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, 30000)
      }
    }
    const resume = () => {
      clearTimeout(retryTimer)
      if (socket) { socket.onclose = null; socket.close(); socket = null }
      historyRequest?.abort()
      if (!document.hidden) connect()
      else setFeed(current => ({ ...current, status: "Paused while this tab is hidden" }))
    }
    const watchdog = setInterval(() => {
      if (!document.hidden && Date.now() - lastMessage > 20000) resume()
    }, 5000)
    connect()
    document.addEventListener("visibilitychange", resume)
    window.addEventListener("online", resume)
    return () => {
      stopped = true; clearTimeout(retryTimer); clearTimeout(tickerTimer); clearInterval(watchdog); historyRequest?.abort()
      if (socket) { socket.onclose = null; socket.close() }
      document.removeEventListener("visibilitychange", resume); window.removeEventListener("online", resume)
      chart.remove()
    }
  }, [instrument, symbol, interval, retry, resolvedTheme, precision, onTicker])

  return <div data-testid="kraken-chart" data-trades={feed.trades} data-candle-updates={feed.candleUpdates} data-last-trade-id={feed.lastTrade?.trade_id} data-candle-time={feed.candle?.time} data-candle-close={feed.candle?.close}>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div><p className="text-xs text-muted-foreground">Kraken · Last traded price</p><p className="mt-1 font-mono text-2xl font-medium tabular-nums">{(feed.lastTrade?.price ?? feed.candle?.close)?.toFixed(precision) ?? "—"}</p></div>
      <div className="flex gap-1 rounded-full bg-muted/50 p-1" role="group" aria-label="Chart timeframe">{KRAKEN_INTERVALS.map(value => <Button key={value} variant={interval === value ? "secondary" : "ghost"} size="sm" className="h-8 rounded-full px-3 text-xs" aria-pressed={interval === value} onClick={() => setIntervalMinutes(value)}>{value === 60 ? "1H" : `${value}m`}</Button>)}</div>
    </div>
    <div ref={container} role="img" aria-label={`${label} Kraken streaming candlestick chart`} className="h-[340px] w-full overflow-hidden rounded-2xl sm:h-[420px]" />
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"><p role="status">{feed.status} · {feed.trades} live trades received</p><Button variant="ghost" size="sm" className="h-7 rounded-full text-[11px]" onClick={() => setRetry(value => value + 1)}>Reconnect</Button></div>
    <p className="mt-1 text-[11px] text-muted-foreground">Kraken exchange prices differ from your broker’s execution quotes. Candles change when trades occur.</p>
    {feed.historyError && <p role="alert" className="mt-2 text-xs text-amber-600">Historical candles could not load. Streamed candles are still shown; reconnect to retry history.</p>}
    <details className="mt-2 text-[10px] text-muted-foreground"><summary className="cursor-pointer">Feed details</summary><p className="mt-1">{feed.candleUpdates} streamed candle updates · Candle close {feed.candle?.close.toFixed(precision) ?? "—"}</p><p>Last trade {feed.lastTrade ? `#${feed.lastTrade.trade_id} · ${feed.lastTrade.timestamp}` : "Waiting for the next trade; a connected socket does not imply trading activity."}</p></details>
  </div>
}
