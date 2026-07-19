import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account, Watchlist } from "@/lib/afd/contracts"
import { MarketsWorkspace, type CalendarData, type NewsData } from "./workspace"
import { PageHeader } from "@/components/desk/page-header"

export const metadata = { title: "Markets" }
export default async function MarketsPage({ searchParams }: { searchParams: Promise<{ instrument?: string }> }) {
  const query = await searchParams
  const initialInstrument = query.instrument?.includes(":") ? query.instrument : "forex:EUR/USD"
  let accounts: Account[] = []; let watchlists: Watchlist[] = []
  let news: NewsData = { items: [], warnings: [] }; let calendar: CalendarData = { items: [], warnings: [] }
  try {
    const request = await authenticatedBackendClient()
    const [a, n, c, w] = await Promise.allSettled([
      request<{ accounts: Account[] }>("/api/trading/accounts"), request<NewsData>("/api/markets/news?limit=12"),
      request<CalendarData>(`/api/markets/calendar?instrument=${encodeURIComponent(initialInstrument)}&limit=20`), request<{ watchlists: Watchlist[] }>("/api/watchlists"),
    ])
    if (a.status === "fulfilled") accounts = a.value.accounts ?? []
    if (n.status === "fulfilled") news = n.value
    if (c.status === "fulfilled") calendar = c.value
    if (w.status === "fulfilled") watchlists = w.value.watchlists ?? []
  } catch { /* independent client sections preserve partial content */ }
  return <div className="space-y-7"><PageHeader eyebrow="Market research" title="Markets workspace" description="Canonical instruments mapped independently to TradingView, Finnhub, and execution providers." /><MarketsWorkspace accounts={accounts} initialWatchlists={watchlists} initialInstrument={initialInstrument} initialNews={news} initialCalendar={calendar} /></div>
}
