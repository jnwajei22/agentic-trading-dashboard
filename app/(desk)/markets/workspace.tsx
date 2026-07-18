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
export type CalendarData = { items: Array<{ event: string; scheduled_at?: string; currency?: string; impact?: string }>; warnings: Warning[]; last_updated?: string }
export type MacroData = { indicators: Array<{ series: { series_id: string; title: string; units?: string }; latest?: { value?: number; date?: string }; source: string }>; warnings: Warning[]; last_updated?: string }
type SearchData = { results: CanonicalInstrument[]; warnings: Warning[] }

const DEFAULT_INSTRUMENT = "forex:EUR/USD"
const canonicalFromStored = (value: string) => value.toLowerCase().startsWith("forex:") ? `forex:${value.split(":", 2)[1].toUpperCase()}` : value.startsWith("OANDA:") ? `forex:${value.split(":")[1].replace("_", "/")}` : value.includes("/") ? `forex:${value}` : value

export function MarketsWorkspace({ accounts, initialWatchlists, initialInstrument, initialNews, initialCalendar, initialMacro }: {
  accounts: Account[]; initialWatchlists: Watchlist[]; initialInstrument: string
  initialNews: NewsData; initialCalendar: CalendarData; initialMacro: MacroData
}) {
  const router = useRouter(); const searchParams = useSearchParams(); const requestSequence = useRef(0)
  const [watchlists, setWatchlists] = useState(initialWatchlists); const [watchlistId, setWatchlistId] = useState(initialWatchlists[0]?.id ?? "")
  const watchlist = watchlists.find((item) => item.id === watchlistId)
  const [selected, setSelected] = useState(initialInstrument || DEFAULT_INSTRUMENT); const [summary, setSummary] = useState<MarketSummary | null>(null)
  const [news, setNews] = useState(initialNews); const [calendar, setCalendar] = useState(initialCalendar); const [macro, setMacro] = useState(initialMacro)
  const [query, setQuery] = useState(""); const [results, setResults] = useState<CanonicalInstrument[]>([]); const [searching, setSearching] = useState(false); const [searched, setSearched] = useState(false)
  const [loading, setLoading] = useState(true); const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({}); const [tradability, setTradability] = useState("")
  const [account, setAccount] = useState(accounts.find((item) => item.is_default_analysis)?.account_alias ?? "")
  const [createOpen, setCreateOpen] = useState(false); const [newName, setNewName] = useState("")

  useEffect(() => { const fromUrl = searchParams.get("instrument"); if (fromUrl && fromUrl !== selected) setSelected(fromUrl) }, [searchParams, selected])
  useEffect(() => {
    const controller = new AbortController(); const sequence = ++requestSequence.current; setLoading(true); setSectionErrors({}); setTradability("")
    const encoded = encodeURIComponent(selected)
    Promise.allSettled([
      browserAfdFetch<MarketSummary>(`markets/${encoded}/summary`, { signal: controller.signal }),
      browserAfdFetch<NewsData>(`markets/news?instrument=${encoded}&limit=12`, { signal: controller.signal }),
      browserAfdFetch<CalendarData>(`markets/calendar?instrument=${encoded}&limit=20`, { signal: controller.signal }),
      browserAfdFetch<MacroData>(`markets/macro?instrument=${encoded}`, { signal: controller.signal }),
      account ? browserAfdFetch<{ available: boolean; currently_tradable: boolean }>(`accounts/${encodeURIComponent(account)}/tradability/${encoded}`, { signal: controller.signal }) : Promise.resolve(null),
    ]).then(([s, n, c, m, t]) => {
      if (sequence !== requestSequence.current) return
      const errors: Record<string, string> = {}
      if (s.status === "fulfilled") setSummary(s.value); else errors.summary = "Market summary is unavailable."
      if (n.status === "fulfilled") setNews(n.value); else errors.news = "News is unavailable."
      if (c.status === "fulfilled") setCalendar(c.value); else errors.calendar = "Calendar context is unavailable."
      if (m.status === "fulfilled") setMacro(m.value); else errors.macro = "Macro context is unavailable."
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
    <Card><CardHeader><div className="flex justify-between"><div><CardTitle>{instrument?.display_symbol ?? selected.split(":", 2)[1]}</CardTitle><p className="text-sm text-muted-foreground">{instrument?.description ?? "Loading canonical instrument…"}</p></div><StatusPill label="TradingView Chart" /></div></CardHeader><CardContent>{loading ? <Skeleton height="h-[460px]" /> : tradingViewSymbol ? <iframe key={tradingViewSymbol} title={`TradingView chart for ${instrument?.display_symbol}`} className="h-[460px] w-full rounded border" src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(tradingViewSymbol)}&interval=60&theme=dark`} /> : <SectionMessage message="TradingView does not have an approved mapping for this instrument." />}<Source label="TradingView visual context; never authoritative for execution" /></CardContent></Card>
  </div>
  <div className="grid gap-5 lg:grid-cols-3"><SummaryCard summary={summary} loading={loading} error={sectionErrors.summary} /><Card><CardHeader><CardTitle>News</CardTitle></CardHeader><CardContent>{sectionErrors.news ? <SectionMessage message={sectionErrors.news} /> : <div className="space-y-3">{news.items.slice(0, 6).map((item, index) => <a key={`${item.headline}-${index}`} href={item.url} target="_blank" rel="noreferrer" className="block text-sm hover:underline">{item.headline}<span className="block text-xs text-muted-foreground">{item.source_name ?? "Finnhub"} · {item.published_at ? new Date(item.published_at).toLocaleString() : "Timestamp unavailable"}</span></a>)}</div>}<Source label="Finnhub" timestamp={news.last_updated} /></CardContent></Card><Card><CardHeader><CardTitle>Economic calendar</CardTitle></CardHeader><CardContent>{sectionErrors.calendar ? <SectionMessage message={sectionErrors.calendar} /> : <div className="space-y-3">{calendar.items.slice(0, 7).map((item, index) => <div key={`${item.event}-${index}`} className="text-sm"><strong>{item.event}</strong><p className="text-xs text-muted-foreground">{item.currency ?? "Global"} · {formatEnum(item.impact)}</p></div>)}</div>}<Source label="Finnhub" timestamp={calendar.last_updated} /></CardContent></Card></div>
  <Card><CardHeader><CardTitle>Macro context</CardTitle></CardHeader><CardContent>{sectionErrors.macro ? <SectionMessage message={sectionErrors.macro} /> : <div className="grid gap-3 sm:grid-cols-3">{macro.indicators.map((item) => <div className="rounded border p-3" key={item.series.series_id}><p className="text-xs text-muted-foreground">{item.series.title}</p><strong className="text-xl">{item.latest?.value ?? "Unavailable"} {item.series.units ?? ""}</strong><p className="text-xs">{item.latest?.date ?? "Date unavailable"}</p></div>)}</div>}<Source label="FRED" timestamp={macro.last_updated} /><div className="mt-4 flex flex-wrap items-center gap-2"><select value={account} onChange={(event) => setAccount(event.target.value)} className="h-10 rounded border bg-background px-3"><option value="">No execution account selected</option>{accounts.map((item) => <option key={item.public_id} value={item.account_alias}>{accountLabel(item)}</option>)}</select>{tradability && <StatusPill label={tradability} />}{sectionErrors.tradability && <span className="text-sm text-amber-600">{sectionErrors.tradability}</span>}<Button asChild variant="outline"><Link href={`/trade?instrument=${encodeURIComponent(selected)}`}>Open in Trade</Link></Button></div></CardContent></Card>
  <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create watchlist</DialogTitle></DialogHeader><Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Watchlist name" /><DialogFooter><Button disabled={!newName.trim()} onClick={() => void create()}>Create Watchlist</Button></DialogFooter></DialogContent></Dialog></div>
}

function SummaryCard({ summary, loading, error }: { summary: MarketSummary | null; loading: boolean; error?: string }) { return <Card><CardHeader><CardTitle>Market Summary</CardTitle></CardHeader><CardContent>{loading ? <Skeleton height="h-40" /> : error ? <SectionMessage message={error} /> : summary?.quote ? <div className="grid grid-cols-2 gap-3"><Datum label="Price" value={summary.quote.price == null ? "Unavailable" : String(summary.quote.price)} /><Datum label="Change" value={summary.quote.change_percent == null ? "Unavailable" : `${summary.quote.change_percent.toFixed(2)}%`} /><Datum label="High" value={summary.quote.high == null ? "Unavailable" : String(summary.quote.high)} /><Datum label="Low" value={summary.quote.low == null ? "Unavailable" : String(summary.quote.low)} /></div> : <SectionMessage message={summary?.sources[0]?.message ?? "No market summary data was returned."} />}<Source label={summary?.sources.map((item) => item.provider).join(", ") || "Finnhub"} timestamp={summary?.sources[0]?.updated_at} status={summary?.sources[0]?.status} /></CardContent></Card> }
function Skeleton({ height }: { height: string }) { return <div aria-label="Loading market data" className={`${height} animate-pulse rounded bg-muted`} /> }
function SectionMessage({ message }: { message: string }) { return <div className="rounded border border-dashed p-4 text-sm text-muted-foreground">{message}</div> }
function Source({ label, timestamp, status }: { label: string; timestamp?: string; status?: string }) { return <p className="mt-3 text-xs text-muted-foreground">Source: {label}{status ? ` · ${formatEnum(status)}` : ""}{timestamp ? ` · Updated ${new Date(timestamp).toLocaleString()}` : ""}</p> }
function Datum({ label, value }: { label: string; value: string }) { return <div className="rounded border p-3"><p className="text-xs text-muted-foreground">{label}</p><strong>{value}</strong></div> }
