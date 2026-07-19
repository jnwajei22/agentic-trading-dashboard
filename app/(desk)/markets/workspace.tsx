"use client"
/* eslint-disable jsx-a11y/role-has-required-aria-props -- selection is represented by URL state and focus */

import Link from "next/link"
import { useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ArrowDown, ArrowUp, Pin, Plus, Search, X } from "lucide-react"
import type { Account, CanonicalInstrument, MarketSummary, Watchlist } from "@/lib/afd/contracts"
import { accountLabel, formatEnum } from "@/lib/afd/display"
import { browserAfdFetch, mutateAfd } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { StatusPill } from "@/components/desk/status-pill"

type Warning = { message?: string }
export type NewsData = { items: Array<{ headline: string; published_at?: string; source_name?: string; url?: string }>; warnings: Warning[]; last_updated?: string }
type FredContext = { series_id:string; label:string; value?:number; previous?:number; trend:number[]; unit?:string; observation_date?:string; source:string; stale:boolean }
type CalendarEvent = { title:string; scheduled_time?:string; country?:string; currency?:string; impact?:string; previous?:number|string; estimate?:number|string; actual?:number|string; unit?:string; source:string; relevant:boolean; relevance_label?:string; related_canonical_instruments:string[]; associated_fred_series:FredContext[] }
export type CalendarData = { items: CalendarEvent[]; warnings: Warning[]; last_updated?: string; sources?:string[] }
type SearchData = { results: CanonicalInstrument[]; warnings: Warning[] }

const DEFAULT_INSTRUMENT = "forex:EUR/USD"
const canonicalFromStored = (value: string) => value.toLowerCase().startsWith("forex:") ? `forex:${value.split(":", 2)[1].toUpperCase()}` : value.startsWith("OANDA:") ? `forex:${value.split(":")[1].replace("_", "/")}` : value.includes("/") ? `forex:${value}` : value

export function MarketsWorkspace({ accounts, initialWatchlists, initialInstrument, initialNews, initialCalendar }: {
  accounts: Account[]; initialWatchlists: Watchlist[]; initialInstrument: string
  initialNews: NewsData; initialCalendar: CalendarData
}) {
  const router = useRouter(); const searchParams = useSearchParams(); const requestSequence = useRef(0)
  const [watchlists, setWatchlists] = useState(initialWatchlists); const [watchlistId, setWatchlistId] = useState(initialWatchlists[0]?.id ?? "")
  const watchlist = watchlists.find((item) => item.id === watchlistId)
  const [selected, setSelected] = useState(initialInstrument || DEFAULT_INSTRUMENT); const [summary, setSummary] = useState<MarketSummary | null>(null)
  const [news, setNews] = useState(initialNews); const [calendar, setCalendar] = useState(initialCalendar)
  const [calendarFilter, setCalendarFilter] = useState<"relevant"|"high"|"all">("relevant"); const [expandedEvent, setExpandedEvent] = useState<string | null>(null)
  const [query, setQuery] = useState(""); const [results, setResults] = useState<CanonicalInstrument[]>([]); const [searching, setSearching] = useState(false); const [searched, setSearched] = useState(false)
  const [loading, setLoading] = useState(true); const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({}); const [tradability, setTradability] = useState("")
  const [account, setAccount] = useState(accounts.find((item) => item.is_default_analysis)?.account_alias ?? "")
  const [createOpen, setCreateOpen] = useState(false); const [newName, setNewName] = useState("")

  useEffect(() => { const fromUrl = searchParams.get("instrument"); if (fromUrl && fromUrl !== selected) setSelected(fromUrl) }, [searchParams, selected])
  useEffect(() => {
    const controller = new AbortController(); const sequence = ++requestSequence.current; setLoading(true); setSectionErrors({}); setTradability("")
    const encoded = encodeURIComponent(selected)
    Promise.allSettled([
      browserAfdFetch<MarketSummary>(`markets/${encoded}/summary${account ? `?account_alias=${encodeURIComponent(account)}` : ""}`, { signal: controller.signal }),
      browserAfdFetch<NewsData>(`markets/news?instrument=${encoded}&limit=12`, { signal: controller.signal }),
      browserAfdFetch<CalendarData>(`markets/calendar?instrument=${encoded}&limit=20`, { signal: controller.signal }),
      account ? browserAfdFetch<{ available: boolean; currently_tradable: boolean }>(`accounts/${encodeURIComponent(account)}/tradability/${encoded}`, { signal: controller.signal }) : Promise.resolve(null),
    ]).then(([s, n, c, t]) => {
      if (sequence !== requestSequence.current) return
      const errors: Record<string, string> = {}
      if (s.status === "fulfilled") setSummary(s.value); else errors.summary = "Market summary is unavailable."
      if (n.status === "fulfilled") setNews(n.value); else errors.news = "News is unavailable."
      if (c.status === "fulfilled") setCalendar(c.value); else errors.calendar = "Calendar context is unavailable."
      if (t.status === "fulfilled" && t.value) setTradability(t.value.available ? (t.value.currently_tradable ? "Tradable now" : "Available; market closed") : "Unavailable on selected account")
      else if (account) errors.tradability = "Account tradability is unavailable."
      setSectionErrors(errors); setLoading(false)
    })
    return () => controller.abort()
  }, [account, selected])
  useEffect(() => {
    if (!query.trim()) { setResults([]); setSearched(false); return }
    const controller = new AbortController(); const timer = setTimeout(async () => { setSearching(true); try { const data = await browserAfdFetch<SearchData>(`markets/search?q=${encodeURIComponent(query)}`, { signal: controller.signal }); setResults(data.results ?? []); setSearched(true) } catch { if (!controller.signal.aborted) { setResults([]); setSearched(true) } } finally { if (!controller.signal.aborted) setSearching(false) } }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [query])

  function choose(canonicalId: string) { setSelected(canonicalId); setResults([]); setQuery(""); router.replace(`/markets?instrument=${encodeURIComponent(canonicalId)}`, { scroll: false }) }
  async function saveItems(items: Watchlist["items"]) { if (!watchlist) return; const updated = await mutateAfd<Watchlist>(`watchlists/${watchlist.id}`, "PUT", { items }); setWatchlists((current) => current.map((item) => item.id === updated.id ? updated : item)) }
  async function add(instrument: CanonicalInstrument) { if (!watchlist || watchlist.items.some((item) => canonicalFromStored(item.symbol) === instrument.canonical_id)) return; await saveItems([...watchlist.items, { symbol: instrument.canonical_id, canonical_id: instrument.canonical_id, position: watchlist.items.length, pinned: false }]) }
  async function create() { const made = await mutateAfd<Watchlist>("watchlists", "POST", { name: newName, symbols: [] }); setWatchlists((current) => [...current, made]); setWatchlistId(made.id); setCreateOpen(false); setNewName("") }
  function move(index: number, delta: number) { if (!watchlist) return; const items = [...watchlist.items]; const target = index + delta; if (target < 0 || target >= items.length) return; [items[index], items[target]] = [items[target], items[index]]; void saveItems(items.map((item, position) => ({ ...item, position }))) }
  const instrument = summary?.instrument; const tradingViewSymbol = instrument?.provider_symbols.tradingview
  return <div className="space-y-5"><div className="grid gap-5 xl:grid-cols-[.75fr_2fr]">
    <Card><CardHeader><div className="flex items-center justify-between"><CardTitle>Watchlists</CardTitle><Button size="icon" variant="ghost" onClick={() => setCreateOpen(true)} aria-label="Create watchlist"><Plus /></Button></div></CardHeader><CardContent>
      <select value={watchlistId} onChange={(event) => setWatchlistId(event.target.value)} className="h-10 w-full rounded border bg-background px-3" aria-label="Watchlist">{watchlists.map((item) => <option key={item.id} value={item.id}>{item.name}{item.is_default ? " · Default" : ""}</option>)}</select>
      <div className="relative mt-3"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && results[0]) choose(results[0].canonical_id); if (event.key === "Escape") setResults([]) }} placeholder="Search symbols or descriptions" className="pl-9 pr-9" aria-label="Market search" role="combobox" aria-expanded={Boolean(results.length)} />{query && <button onClick={() => setQuery("")} aria-label="Clear search" className="absolute right-3 top-3"><X className="h-4 w-4" /></button>}
        {(searching || results.length > 0 || searched) && <div role="listbox" className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded border bg-popover shadow-lg">{searching ? <p className="p-3 text-sm">Searching…</p> : results.map((item) => <div key={item.canonical_id} className="flex items-center border-b"><button role="option" className="min-w-0 flex-1 p-3 text-left" onClick={() => choose(item.canonical_id)}><strong>{item.display_symbol}</strong><span className="block text-xs text-muted-foreground">{item.description} · {formatEnum(item.asset_class)}{item.exchange ? ` · ${item.exchange}` : ""}</span></button><Button size="sm" variant="ghost" onClick={() => void add(item)}>Add</Button></div>)}{searched && !results.length && <p className="p-3 text-sm">No matching markets.</p>}</div>}
      </div>
      <div className="mt-4 space-y-1">{watchlist?.items.map((item, index) => { const canonical = canonicalFromStored(item.canonical_id ?? item.symbol); const active = canonical === selected; return <div key={item.symbol} className={`flex items-center gap-1 rounded border p-1 ${active ? "border-emerald-500 bg-emerald-500/10" : ""}`}><button className="min-w-0 flex-1 truncate px-2 text-left text-sm" aria-current={active} onClick={() => choose(canonical)}>{canonical.split(":", 2)[1] ?? item.symbol}</button><Button size="icon" variant="ghost" onClick={() => void saveItems(watchlist.items.map((entry) => entry.symbol === item.symbol ? { ...entry, pinned: !entry.pinned } : entry))} aria-label={`Pin ${item.symbol}`}><Pin className={item.pinned ? "fill-current" : ""} /></Button><Button size="icon" variant="ghost" onClick={() => move(index, -1)} aria-label={`Move ${item.symbol} up`}><ArrowUp /></Button><Button size="icon" variant="ghost" onClick={() => move(index, 1)} aria-label={`Move ${item.symbol} down`}><ArrowDown /></Button></div> })}{!watchlist?.items.length && <div className="rounded border border-dashed p-5 text-center text-sm"><p>This watchlist is empty.</p><Button variant="link" onClick={() => document.querySelector<HTMLInputElement>('[aria-label="Market search"]')?.focus()}>Search markets</Button></div>}</div>
    </CardContent></Card>
    <Card><CardHeader><div className="flex justify-between"><div><CardTitle>{instrument?.display_symbol ?? selected.split(":", 2)[1]}</CardTitle><p className="text-sm text-muted-foreground">{instrument?.description ?? "Loading canonical instrument…"}</p></div></div></CardHeader><CardContent>{loading ? <Skeleton height="h-[460px]" /> : tradingViewSymbol ? <iframe key={tradingViewSymbol} title={`TradingView chart for ${instrument?.display_symbol}`} className="h-[460px] w-full rounded border" src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(tradingViewSymbol)}&interval=60&theme=dark`} /> : <SectionMessage message="TradingView does not have an approved mapping for this instrument." />}<Source label="TradingView visual context; never authoritative for execution" /></CardContent></Card>
  </div>
  <div className="grid gap-5 lg:grid-cols-3"><SummaryCard summary={summary} loading={loading} error={sectionErrors.summary} /><Card><CardHeader><CardTitle>Relevant News</CardTitle></CardHeader><CardContent>{sectionErrors.news ? <SectionMessage message={sectionErrors.news} /> : <div className="space-y-3">{news.items.slice(0, 6).map((item, index) => <a key={`${item.headline}-${index}`} href={item.url} target="_blank" rel="noreferrer" className="block text-sm hover:underline">{item.headline}<span className="block text-xs text-muted-foreground">{item.source_name ?? "Finnhub"} · {item.published_at ? new Date(item.published_at).toLocaleString() : "Timestamp unavailable"}</span></a>)}</div>}<Source label="Finnhub" timestamp={news.last_updated} /></CardContent></Card><EconomicCalendar data={calendar} error={sectionErrors.calendar} filter={calendarFilter} setFilter={setCalendarFilter} expanded={expandedEvent} setExpanded={setExpandedEvent} /></div>
  <Card><CardHeader><CardTitle>Tradability</CardTitle></CardHeader><CardContent><div className="flex flex-wrap items-center gap-2"><select value={account} onChange={(event) => setAccount(event.target.value)} className="h-10 rounded border bg-background px-3" aria-label="Execution account"><option value="">No execution account selected</option>{accounts.map((item) => <option key={item.public_id} value={item.account_alias}>{accountLabel(item)}{item.is_default_analysis ? " · Default" : ""}</option>)}</select>{tradability && <StatusPill label={tradability} />}{sectionErrors.tradability && <span className="text-sm text-amber-600">{sectionErrors.tradability}</span>}<Button asChild variant="outline"><Link href={`/trade?instrument=${encodeURIComponent(selected)}`}>Open in Trade</Link></Button></div></CardContent></Card>
  <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create watchlist</DialogTitle></DialogHeader><Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Watchlist name" /><DialogFooter><Button disabled={!newName.trim()} onClick={() => void create()}>Create Watchlist</Button></DialogFooter></DialogContent></Dialog></div>
}

function SummaryCard({ summary, loading, error }: { summary: MarketSummary | null; loading: boolean; error?: string }) { return <Card><CardHeader><CardTitle>Market Summary</CardTitle></CardHeader><CardContent>{loading ? <Skeleton height="h-40" /> : error ? <SectionMessage message={error} /> : <div className="space-y-4">{summary?.quote ? <div className="grid grid-cols-2 gap-3"><Datum label="Price" value={String(summary.quote.price)} /><Datum label="Change" value={summary.quote.change_percent == null ? "Unavailable" : `${summary.quote.change_percent.toFixed(2)}%`} /><Datum label="High" value={summary.quote.high == null ? "Unavailable" : String(summary.quote.high)} /><Datum label="Low" value={summary.quote.low == null ? "Unavailable" : String(summary.quote.low)} /></div> : <p className="text-sm text-muted-foreground">Quote unavailable. Economic drivers remain available below.</p>}<div className="space-y-2">{summary?.drivers?.map((driver) => <div key={driver.series_id} className="rounded border p-3 text-sm"><div className="flex justify-between gap-2"><strong>{driver.label}</strong><span>{driver.value} {driver.unit}</span></div><p className="text-xs text-muted-foreground">{driver.applies_to} · {formatEnum(driver.category)} · {driver.observation_date}{driver.stale ? " · Stale" : " · Current"} · {driver.source}</p></div>)}</div></div>}<Source label={summary?.sources.map((item) => item.provider).join(", ") || "Finnhub / FRED"} timestamp={summary?.sources[0]?.updated_at} status={summary?.partial ? "partial" : summary?.sources[0]?.status} /></CardContent></Card> }

function EconomicCalendar({ data, error, filter, setFilter, expanded, setExpanded }: { data:CalendarData; error?:string; filter:"relevant"|"high"|"all"; setFilter:(value:"relevant"|"high"|"all")=>void; expanded:string|null; setExpanded:(value:string|null)=>void }) {
  const items = data.items.filter((item) => filter === "all" || (filter === "relevant" ? item.relevant : item.impact?.toLowerCase() === "high"))
  return <Card><CardHeader><CardTitle>Economic Calendar</CardTitle></CardHeader><CardContent><div className="mb-3 flex flex-wrap gap-1">{([["relevant","Relevant to Selected Market"],["high","High Impact"],["all","All Events"]] as const).map(([value,label]) => <Button key={value} size="sm" variant={filter === value ? "default" : "outline"} onClick={() => setFilter(value)}>{label}</Button>)}</div>{error ? <SectionMessage message={error} /> : <div className="space-y-3">{items.slice(0, 8).map((item, index) => { const key=`${item.title}-${item.scheduled_time}-${index}`; return <div key={key} className="rounded border p-3 text-sm"><button className="w-full text-left" onClick={() => setExpanded(expanded === key ? null : key)} aria-expanded={expanded === key}><strong>{item.title}</strong><p className="text-xs text-muted-foreground">{item.currency ?? "Global"} · {formatEnum(item.impact)}{item.scheduled_time ? ` · ${new Date(item.scheduled_time).toLocaleString()}` : ""}</p>{item.relevance_label && <p className="mt-1 text-xs text-emerald-600">{item.relevance_label}</p>}</button>{expanded === key && item.associated_fred_series.map((series) => <div key={series.series_id} className="mt-2 rounded bg-muted p-2 text-xs"><strong>{series.label}</strong><p>Latest: {series.value ?? "Unavailable"} {series.unit ?? ""} · Previous: {series.previous ?? "Unavailable"}</p><p>Recent trend: {series.trend.join(" → ") || "Unavailable"}</p><p>{series.source} · {series.observation_date ?? "Date unavailable"}{series.stale ? " · Stale" : " · Current"}</p></div>)}</div>})}{!items.length && <SectionMessage message="No events match this filter." />}</div>}<Source label={data.sources?.join(", ") || "Finnhub / FRED"} timestamp={data.last_updated} /></CardContent></Card>
}
function Skeleton({ height }: { height: string }) { return <div aria-label="Loading market data" className={`${height} animate-pulse rounded bg-muted`} /> }
function SectionMessage({ message }: { message: string }) { return <div className="rounded border border-dashed p-4 text-sm text-muted-foreground">{message}</div> }
function Source({ label, timestamp, status }: { label: string; timestamp?: string; status?: string }) { return <p className="mt-3 text-xs text-muted-foreground">Source: {label}{status ? ` · ${formatEnum(status)}` : ""}{timestamp ? ` · Updated ${new Date(timestamp).toLocaleString()}` : ""}</p> }
function Datum({ label, value }: { label: string; value: string }) { return <div className="rounded border p-3"><p className="text-xs text-muted-foreground">{label}</p><strong>{value}</strong></div> }
