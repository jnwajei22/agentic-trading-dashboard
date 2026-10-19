"use client"

import { useEffect, useState } from "react"
import { RotateCw } from "lucide-react"
import { browserAfdFetch } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"

type Candle = { timestamp: number; open: number; high: number; low: number; close: number }
type ChartData = { instrument: string; timeframe: string; source: string; checked_at: string; quote: { price: number; bid: number | null; ask: number | null; timestamp: string | null } | null; candles: Candle[]; warnings: string[] }

export function BrokerMarketChart({ instrument, accountAlias, label }: { instrument: string; accountAlias: string; label: string }) {
  const [timeframe, setTimeframe] = useState("5m")
  const [data, setData] = useState<ChartData | null>(null)
  const [error, setError] = useState("")
  const [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(true)
  const [clock, setClock] = useState(Date.now())
  useEffect(() => {
    const controller = new AbortController()
    let stopped = false, running = false, count = 0
    let timer: ReturnType<typeof setTimeout>
    setData(null); setLoading(true); setError("")
    async function refresh() {
      clearTimeout(timer)
      if (running || stopped) return
      if (document.hidden) { timer = setTimeout(refresh, 10000); return }
      running = true
      try {
        const params = new URLSearchParams({ instrument, account_alias: accountAlias, timeframe, history: String(count % 6 === 0) })
        const next = await browserAfdFetch<ChartData>(`markets/chart?${params}`, { signal: controller.signal })
        if (stopped) return
        setData(previous => ({ ...next, candles: next.candles.length ? next.candles : previous?.candles ?? [] }))
        setError(""); count++
      } catch { if (!stopped) setError("The broker feed paused. Retrying automatically.") }
      finally { running = false; if (!stopped) { setLoading(false); timer = setTimeout(refresh, 10000) } }
    }
    const resume = () => { if (!document.hidden) void refresh() }
    void refresh()
    document.addEventListener("visibilitychange", resume)
    window.addEventListener("online", resume)
    return () => { stopped = true; clearTimeout(timer); controller.abort(); document.removeEventListener("visibilitychange", resume); window.removeEventListener("online", resume) }
  }, [instrument, accountAlias, timeframe, retry])
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer) }, [])
  const pending = data?.instrument === instrument && data.timeframe === timeframe ? data : null
  const quote = pending?.quote
  const delayed = Boolean(quote?.timestamp && clock - new Date(quote.timestamp).getTime() > 45000)
  const missed = Boolean(pending && clock - new Date(pending.checked_at).getTime() > 45000)
  const precision = instrument.endsWith("JPY") ? 3 : 5
  const format = (value: number) => value.toFixed(precision)
  return <div>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div><p className="text-[10px] uppercase tracking-wider text-muted-foreground">Broker price</p><p className="mt-1 font-mono text-2xl font-medium tabular-nums">{quote ? format(quote.price) : "—"}</p>{quote && <p className="mt-1 text-[11px] text-muted-foreground">Bid {quote.bid == null ? "—" : format(quote.bid)} · Ask {quote.ask == null ? "—" : format(quote.ask)}</p>}</div>
      <div className="flex gap-1 rounded-full bg-muted/50 p-1" role="group" aria-label="Chart timeframe">{["1m", "5m", "15m", "1H"].map(value => <Button key={value} variant={timeframe === value ? "secondary" : "ghost"} size="sm" className="h-8 rounded-full px-3 text-xs" aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</Button>)}</div>
    </div>
    <div className="h-[340px] sm:h-[420px]" aria-busy={loading}>
      {pending?.candles.length ? <CandlePlot candles={pending.candles} price={quote?.price} label={label} precision={precision} /> : <div className="flex h-full items-center justify-center rounded-2xl bg-muted/30 p-6 text-center text-sm text-muted-foreground">{loading ? "Loading broker candles…" : error || "The broker returned no candle history. Its latest quote is shown above."}</div>}
    </div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"><p>{error || (missed ? "Waiting for the broker feed…" : delayed ? "The broker’s quote timestamp is delayed." : "TradeLocker quotes refresh every 10 seconds.")}{pending && ` · Checked ${new Date(pending.checked_at).toLocaleTimeString()}`}</p><Button variant="ghost" size="sm" className="h-7 gap-1.5 rounded-full text-[11px]" onClick={() => setRetry(value => value + 1)}><RotateCw className="h-3 w-3" />Reconnect feed</Button></div>
    {pending?.candles.at(-1) && <p className="mt-1 text-[10px] text-muted-foreground">Last broker candle: {new Date(pending.candles.at(-1)!.timestamp).toLocaleString()}. Candles refresh each minute; the dashed line tracks the latest quote.</p>}
    {pending?.warnings.length ? <p className="mt-2 text-xs text-amber-600">{pending.warnings.join(" ")}</p> : null}
  </div>
}

function CandlePlot({ candles, price, label, precision }: { candles: Candle[]; price?: number; label: string; precision: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const points = candles.slice(-100)
  const values = points.flatMap(row => [row.low, row.high])
  if (price != null) values.push(price)
  const min = Math.min(...values), max = Math.max(...values)
  const pad = Math.max((max - min) * .1, 10 ** -precision * 10)
  const low = min - pad, high = max + pad
  const y = (value: number) => 18 + (high - value) / (high - low) * 342
  const width = 900 / Math.max(points.length, 1)
  const selected = hover == null ? points.at(-1) : points[Math.min(hover, points.length - 1)]
  return <div className="relative h-full"><svg role="img" aria-label={`${label} broker candlestick chart`} viewBox="0 0 1000 410" preserveAspectRatio="none" className="h-full w-full" onPointerMove={event => { const box = event.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(points.length - 1, Math.floor((event.clientX - box.left) / box.width * 1000 / width)))) }} onPointerLeave={() => setHover(null)}>
    {Array.from({ length: 5 }, (_, index) => { const value = low + (high - low) * index / 4; return <g key={index}><line x1="0" x2="905" y1={y(value)} y2={y(value)} className="stroke-border/40" /><text x="920" y={y(value) + 4} className="fill-muted-foreground text-[12px]">{value.toFixed(precision)}</text></g> })}
    {points.map((row, index) => { const x = index * width + width / 2; const rising = row.close >= row.open; const color = rising ? "#10b981" : "#ef4444"; return <g key={row.timestamp}><title>{new Date(row.timestamp).toLocaleString()} · O {row.open} H {row.high} L {row.low} C {row.close}</title><line x1={x} x2={x} y1={y(row.high)} y2={y(row.low)} stroke={color} strokeWidth="1.2" /><rect x={x - Math.max(1, width * .3)} y={Math.min(y(row.open), y(row.close))} width={Math.max(2, width * .6)} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={color} /></g> })}
    {price != null && <g><line x1="0" x2="908" y1={y(price)} y2={y(price)} stroke="#10b981" strokeDasharray="5 5" opacity=".7" /><rect x="912" y={y(price) - 10} width="86" height="20" rx="4" fill="#059669" /><text x="918" y={y(price) + 4} className="fill-white text-[12px]">{price.toFixed(precision)}</text></g>}
    {hover != null && <line x1={hover * width + width / 2} x2={hover * width + width / 2} y1="18" y2="360" className="stroke-muted-foreground/40" strokeDasharray="3 3" />}
    {[0, Math.floor(points.length / 2), points.length - 1].map((index, i) => <text key={i} x={index * width + width / 2} y="395" textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} className="fill-muted-foreground text-[12px]">{new Date(points[index].timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text>)}
  </svg>{selected && <p className="pointer-events-none absolute left-1 top-0 max-w-[90%] rounded-md bg-card/90 px-2 py-1 font-mono text-[10px] text-muted-foreground">O {selected.open.toFixed(precision)} · H {selected.high.toFixed(precision)} · L {selected.low.toFixed(precision)} · C {selected.close.toFixed(precision)}</p>}</div>
}
