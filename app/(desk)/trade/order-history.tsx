"use client"

import { Fragment, useState } from "react"
import { ChevronDown, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { activityLabel, activityNumber, eventTime, historyRows, statusGroup, type BrokerRow } from "./activity-model"

const tones = {
  filled: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  cancelled: "bg-muted text-muted-foreground",
  rejected: "bg-red-500/10 text-red-600 dark:text-red-400",
  other: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
}

export function OrderHistory({ rows }: { rows: BrokerRow[] }) {
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState("all")
  const [expanded, setExpanded] = useState<string | null>(null)
  const [limit, setLimit] = useState(20)
  const filtered = historyRows(rows, query, filter)
  const filled = rows.filter(row => statusGroup(row.status) === "filled").length

  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs leading-relaxed text-muted-foreground">{rows.length} broker events · {filled} filled<span className="mt-1 block">Entries, exits, and protective orders are separate events.</span></p>
      <label className="flex w-full items-center gap-2 rounded-full border border-border/60 bg-background/50 px-3 py-2 sm:w-64">
        <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <input aria-label="Search order history" placeholder="Search market or order ID" value={query} onChange={event => {setQuery(event.target.value);setLimit(20)}} className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground" />
      </label>
    </div>
    <div className="flex flex-wrap gap-1" aria-label="Filter order history">
      {[["all", "All events"], ["filled", "Filled"], ["cancelled", "Cancelled / expired"], ["rejected", "Rejected"]].map(([value, name]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => {setFilter(value);setLimit(20)}} className={`rounded-full px-3 py-1.5 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring ${filter === value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted"}`}>{name}</button>)}
    </div>
    {!filtered.length ? <p className="py-8 text-center text-sm text-muted-foreground">No events match these filters.</p> : <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <caption className="sr-only">Broker order history, newest available event first. Expand an order for its ID and protection details.</caption>
        <thead className="text-[11px] text-muted-foreground"><tr>{["Market / order", "Volume", "Price", "Status", "Updated"].map(name => <th key={name} scope="col" className="px-3 pb-3 font-normal">{name}</th>)}</tr></thead>
        <tbody>{filtered.slice(0, limit).map((row, index) => {
          const key = `${row.id}-${index}`
          const open = expanded === key
          const time = eventTime(row.time)
          return <Fragment key={key}>
            <tr className="border-t border-border/40 transition-colors hover:bg-muted/20">
              <td className="px-3 py-3"><button type="button" onClick={() => setExpanded(open ? null : key)} aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} details for ${row.symbol} order ${row.id}`} className="flex items-center gap-3 rounded-lg text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
                <ChevronDown aria-hidden="true" className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
                <span><span className="font-medium">{row.symbol}</span><span className="mt-0.5 flex gap-1.5 text-xs"><span className={row.side?.toLowerCase() === "buy" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>{activityLabel(row.side)}</span><span className="text-muted-foreground">· {activityLabel(row.type)}</span></span></span>
              </button></td>
              <td className="px-3 py-3 tabular-nums">{activityNumber(row.quantity, 4)} <span className="text-xs text-muted-foreground">lots</span></td>
              <td className="px-3 py-3 tabular-nums">{activityNumber(row.entry, 8)}</td>
              <td className="px-3 py-3"><span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ${tones[statusGroup(row.status)]}`}>{activityLabel(row.status)}</span></td>
              <td className="whitespace-nowrap px-3 py-3 text-xs text-muted-foreground">{time ? <time dateTime={new Date(time).toISOString()} title={new Date(time).toLocaleString()}><span className="block">{new Date(time).toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"})}</span><span className="mt-0.5 block tabular-nums">{new Date(time).toLocaleTimeString(undefined, {hour:"numeric", minute:"2-digit"})}</span></time> : "Unavailable"}</td>
            </tr>
            {open && <tr><td colSpan={5} className="bg-muted/20 px-6 py-4"><dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs md:grid-cols-4">
              {Object.entries({"Broker order ID":row.id, "Stop loss":activityNumber(row.stop_loss,8), "Take profit":activityNumber(row.take_profit,8), "Broker-reported P&L":activityNumber(row.pnl,2)}).map(([name, value]) => <div key={name}><dt className="text-muted-foreground">{name}</dt><dd className="mt-1 break-all font-medium tabular-nums">{value}</dd></div>)}
            </dl></td></tr>}
          </Fragment>
        })}</tbody>
      </table>
    </div>}
    {filtered.length > limit && <Button variant="ghost" className="w-full rounded-full text-xs" onClick={() => setLimit(n => n + 20)}>Show more · {filtered.length - limit} remaining</Button>}
  </div>
}
