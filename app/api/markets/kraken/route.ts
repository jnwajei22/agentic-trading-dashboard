import { NextRequest, NextResponse } from "next/server"
import { KRAKEN_INTERVALS, krakenSymbol, parseKrakenHistory } from "@/lib/afd/kraken-market-data"

export const maxDuration = 30

export async function GET(request: NextRequest) {
  const symbol = krakenSymbol(request.nextUrl.searchParams.get("instrument") ?? "")
  const interval = Number(request.nextUrl.searchParams.get("interval") ?? 5)
  if (!symbol || !KRAKEN_INTERVALS.some(value => value === interval)) return NextResponse.json({ error: "Unsupported Kraken market or timeframe." }, { status: 400 })
  try {
    const url = new URL("https://api.kraken.com/0/public/OHLC")
    url.searchParams.set("pair", symbol.replace("/", ""))
    url.searchParams.set("interval", String(interval))
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.any([request.signal, AbortSignal.timeout(15000)]) })
    if (!response.ok) throw new Error("Kraken request failed")
    const candles = parseKrakenHistory(await response.json())
    return NextResponse.json({ source: "Kraken", symbol, interval, candles }, { headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json({ error: "Kraken history is unavailable. Live streaming will retry automatically." }, { status: 502 })
  }
}
