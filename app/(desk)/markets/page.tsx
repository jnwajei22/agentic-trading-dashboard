import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account } from "@/lib/afd/contracts"
import { MarketsWorkspace, type CalendarData, type MacroData, type NewsData, type OverviewData } from "./workspace"
import { PageHeader } from "@/components/desk/page-header"

export const metadata = { title: "Markets" }
const empty = { warnings: [] }
export default async function MarketsPage() {
  let accounts: Account[]=[]; let overview:OverviewData={...empty,quotes:[]}; let news:NewsData={...empty,items:[]}; let calendar:CalendarData={...empty,items:[]}; let macro:MacroData={...empty,indicators:[]}
  try { const request=await authenticatedBackendClient(); const [a,o,n,c,m]=await Promise.all([
    request<{accounts:Account[]}>("/api/broker/accounts"), request<OverviewData>("/api/markets/overview"),
    request<NewsData>("/api/markets/news?limit=12"), request<CalendarData>("/api/markets/calendar?limit=20"), request<MacroData>("/api/markets/macro")])
    accounts=a.accounts??[]; overview=o; news=n; calendar=c; macro=m
  } catch { /* independent workspace sections render safely */ }
  return <div className="space-y-7"><PageHeader eyebrow="Market research" title="Markets workspace" description="Provider-agnostic research from TradingView, Finnhub, and FRED. A broker connection is only needed to check account tradability." /><MarketsWorkspace accounts={accounts} overview={overview} news={news} calendar={calendar} macro={macro} /></div>
}
