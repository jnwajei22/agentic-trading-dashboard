"use client"

import { KrakenMarketChart } from "./kraken-market-chart"
import { krakenSymbol, type KrakenTicker } from "@/lib/afd/kraken-market-data"

export function MarketChart({ label, instrument, onTicker }: { label: string; instrument: string; onTicker?: (ticker: KrakenTicker) => void }) {
  const symbol = krakenSymbol(instrument)
  if (!symbol) return <div className="flex h-[420px] items-center justify-center rounded-2xl bg-muted/30 p-8 text-center"><div><p className="font-medium">This market isn’t available on Kraken.</p><p className="mt-2 text-sm text-muted-foreground">Choose a supported market from your watchlist. Chart prices may differ from your broker’s execution quotes.</p></div></div>
  return <KrakenMarketChart key={instrument} instrument={instrument} symbol={symbol} label={label} onTicker={onTicker} />
}
