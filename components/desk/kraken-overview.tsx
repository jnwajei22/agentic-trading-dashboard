"use client"

import { useEffect, useState } from "react"
import { krakenSymbol, type KrakenTicker } from "@/lib/afd/kraken-market-data"

export function KrakenOverview({ instrument, ticker }: { instrument: string; ticker: KrakenTicker | null }) {
  const [clock, setClock] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 5000); return () => clearInterval(timer) }, [])
  const symbol = krakenSymbol(instrument)
  const data = ticker?.symbol === symbol ? ticker : null
  if (!symbol) return <p className="py-5 text-sm text-muted-foreground">Kraken doesn’t list this instrument. Pricing and statistics are unavailable; no other feed is substituted.</p>
  if (!data) return <p role="status" className="py-5 text-sm text-muted-foreground">Waiting for Kraken’s market snapshot… The chart and Overview share one live connection.</p>
  const precision = symbol.endsWith("JPY") ? 3 : symbol.startsWith("BTC") || symbol.startsWith("ETH") ? 2 : 5
  const price = (value: number) => value.toFixed(precision)
  const spread = data.ask - data.bid
  const range = data.high - data.low
  const position = range > 0 ? Math.max(0, Math.min(100, (data.last - data.low) / range * 100)) : null
  const delayed = clock - Date.parse(data.checkedAt) > 45000
  const amount = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, notation: "compact" }).format(data.volume)
  return <div data-testid="kraken-overview" data-symbol={symbol} data-price={data.last} className="space-y-6">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs text-muted-foreground">Kraken · Last traded price</p><div className="mt-1 flex items-baseline gap-3"><p className="text-3xl font-medium tracking-tight tabular-nums">{price(data.last)}</p><span className={`text-sm tabular-nums ${data.changePercent >= 0 ? "text-emerald-600" : "text-red-500"}`}>{data.changePercent > 0 ? "+" : ""}{data.changePercent.toFixed(2)}% <span className="text-muted-foreground">24h</span></span></div></div><p className="text-[11px] text-muted-foreground">{delayed ? "Updates paused · showing the last received snapshot" : "Live market statistics"}<br />Received {new Date(data.checkedAt).toLocaleTimeString()}</p></div>
    <div className="grid grid-cols-2 gap-x-8 gap-y-5 lg:grid-cols-4">{[["Best bid",price(data.bid)],["Best ask",price(data.ask)],["Spread",price(spread)],[`24h volume · ${symbol.split("/")[0]}`,amount]].map(([label,value])=><div key={label}><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-mono text-base tabular-nums">{value}</p></div>)}</div>
    <div className="rounded-2xl bg-muted/35 p-5"><div className="flex items-center justify-between text-xs"><span className="text-muted-foreground">24-hour trading range</span><span>{position == null ? "No range yet" : `${position.toFixed(0)}% of the way from low to high`}</span></div><div className="relative my-3 h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-emerald-500/35" style={{width:`${position ?? 0}%`}} />{position != null && <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-500 ring-4 ring-background" style={{left:`${position}%`}} />}</div><div className="flex justify-between font-mono text-xs tabular-nums"><span>{price(data.low)}</span><span>{price(data.high)}</span></div></div>
    <p className="text-sm leading-relaxed text-muted-foreground">{symbol} is {data.changePercent >= 0 ? "up" : "down"} {Math.abs(data.changePercent).toFixed(2)}% over 24 hours and trading {data.last >= data.vwap ? "above" : "below"} its volume-weighted average of {price(data.vwap)}. These statistics describe Kraken’s exchange activity; they aren’t a trade recommendation.</p>
    <p className="text-[10px] text-muted-foreground">Source: Kraken · Rolling 24-hour window · Exchange volume, not total global forex volume. Orders use your execution broker’s quotes.</p>
  </div>
}
