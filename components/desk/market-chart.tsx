"use client"

import { useEffect, useRef, useState } from "react"
import { useTheme } from "next-themes"
import { RotateCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { BrokerMarketChart } from "./broker-market-chart"

export function MarketChart({ symbol, label, instrument, accountAlias }: { symbol: string; label: string; instrument: string; accountAlias?: string }) {
  const [view, setView] = useState("broker")
  return <div>
    {accountAlias && <div className="mb-5 flex w-fit gap-1 rounded-full bg-muted/50 p-1" role="group" aria-label="Chart source">{[["broker", "Your broker"], ["tradingview", "TradingView"]].map(([value, title]) => <Button key={value} variant={view === value ? "secondary" : "ghost"} size="sm" className="h-8 rounded-full px-4 text-xs" aria-pressed={view === value} onClick={() => setView(value)}>{title}</Button>)}</div>}
    {accountAlias && view === "broker" ? <BrokerMarketChart key={`${accountAlias}:${instrument}`} instrument={instrument} accountAlias={accountAlias} label={label} /> : <TradingViewChart symbol={symbol} label={label} />}
  </div>
}

function TradingViewChart({ symbol, label }: { symbol: string; label: string }) {
  const container = useRef<HTMLDivElement>(null)
  const { resolvedTheme } = useTheme()
  const [reload, setReload] = useState(0)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const target = container.current
    if (!target) return
    setFailed(false)
    const widget = document.createElement("div")
    widget.className = "tradingview-widget-container__widget"
    widget.style.height = "100%"
    const script = document.createElement("script")
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
    script.async = true
    script.onerror = () => setFailed(true)
    script.textContent = JSON.stringify({ autosize: true, symbol, interval: "5", timezone: "Etc/UTC", theme: resolvedTheme === "light" ? "light" : "dark", style: "1", locale: "en", allow_symbol_change: false, calendar: false, details: false, hide_side_toolbar: true, hide_volume: true, backgroundColor: resolvedTheme === "light" ? "#ffffff" : "#171b1e", gridColor: "rgba(120,130,140,0.06)", support_host: "https://www.tradingview.com" })
    target.replaceChildren(widget, script)
    return () => { script.onerror = null; target.replaceChildren() }
  }, [symbol, resolvedTheme, reload])
  return <div>
    <div className="h-[420px] sm:h-[500px]"><div ref={container} aria-label={`${label} price chart`} className="tradingview-widget-container h-full w-full overflow-hidden rounded-2xl" /></div>
    {failed && <p role="alert" className="mt-3 text-xs text-muted-foreground">The chart could not connect. Check your connection and try reconnecting.</p>}
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
      <p><a href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`} target="_blank" rel="noopener noreferrer" className="hover:text-foreground">Chart by TradingView</a> · Updates with the available market feed</p>
      <Button variant="ghost" size="sm" className="h-7 gap-1.5 rounded-full text-[11px]" onClick={() => setReload(value => value + 1)}><RotateCw className="h-3 w-3" />Reconnect chart</Button>
    </div>
    <p className="mt-1 text-[10px] text-muted-foreground">Chart prices may differ from your broker’s execution quotes.</p>
  </div>
}
