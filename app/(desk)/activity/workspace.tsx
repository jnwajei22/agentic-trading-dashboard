"use client"

import { useMemo, useState } from "react"
import type { Account, ActivityEvent, ProfileSummary, RunAudit } from "@/lib/afd/contracts"
import { accountTitle, formatEnum } from "@/lib/afd/display"
import { browserAfdFetch } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { StatusPill } from "@/components/desk/status-pill"

const PAGE_SIZE = 25

export function ActivityWorkspace({ initialEvents, accounts, profiles }: {
  initialEvents: ActivityEvent[]
  accounts: Account[]
  profiles: ProfileSummary[]
}) {
  const [account, setAccount] = useState("")
  const [profile, setProfile] = useState("")
  const [type, setType] = useState("")
  const [outcome, setOutcome] = useState("")
  const [environment, setEnvironment] = useState("")
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [page, setPage] = useState(0)
  const [audit, setAudit] = useState<RunAudit | null>(null)
  const filtered = useMemo(() => initialEvents.filter((event) =>
    (!account || event.account_alias === account) &&
    (!profile || event.profile_id === profile) &&
    (!type || event.event_type === type) &&
    (!outcome || event.outcome === outcome) &&
    (!environment || event.environment === environment) &&
    (!from || event.occurred_at.slice(0, 10) >= from) &&
    (!to || event.occurred_at.slice(0, 10) <= to)
  ), [account, environment, from, initialEvents, outcome, profile, to, type])
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const types = [...new Set(initialEvents.map((event) => event.event_type))]
  const outcomes = [...new Set(initialEvents.map((event) => event.outcome).filter(Boolean))] as string[]

  async function inspect(event: ActivityEvent) {
    if (event.run_id) setAudit(await browserAfdFetch<RunAudit>(`autonomous-runs/${event.run_id}/audit`))
  }

  function csv() {
    const headers = ["timestamp", "event_type", "account_number", "strategy", "environment", "outcome", "symbol", "reasons"]
    const rows = filtered.map((event) => [event.occurred_at, event.event_type, event.account_number ?? "", event.strategy_name ?? "", event.environment ?? "", event.outcome ?? "", event.symbol ?? "", (event.reason_codes ?? []).join("|")])
    const escape = (value: string) => `"${value.replaceAll('"', '""')}"`
    const blob = new Blob([[headers, ...rows].map((row) => row.map((value) => escape(String(value))).join(",")).join("\n")], { type: "text/csv" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = "agentic-trading-activity.csv"
    link.click()
    URL.revokeObjectURL(url)
  }

  const resetPage = (setter: (value: string) => void) => (value: string) => { setter(value); setPage(0) }
  return <div className="space-y-5">
    <Card><CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
      <Choice label="Filter by account" value={account} onChange={resetPage(setAccount)} options={accounts.map((item) => [item.account_alias, accountTitle(item)])} empty="All accounts" />
      <Choice label="Filter by strategy" value={profile} onChange={resetPage(setProfile)} options={profiles.map((item) => [item.public_id, item.name])} empty="All strategies" />
      <Choice label="Filter by event type" value={type} onChange={resetPage(setType)} options={types.map((item) => [item, formatEnum(item)])} empty="All event types" />
      <Choice label="Filter by outcome" value={outcome} onChange={resetPage(setOutcome)} options={outcomes.map((item) => [item, formatEnum(item)])} empty="All outcomes" />
      <Choice label="Filter by environment" value={environment} onChange={resetPage(setEnvironment)} options={[["demo", "Demo"], ["live", "Live"]]} empty="Demo and Live" />
      <Input type="date" value={from} onChange={(event) => resetPage(setFrom)(event.target.value)} aria-label="From date" />
      <Input type="date" value={to} onChange={(event) => resetPage(setTo)(event.target.value)} aria-label="To date" />
      <Button variant="outline" onClick={csv} disabled={!filtered.length}>Export CSV</Button>
    </CardContent></Card>
    <div className="space-y-2">
      {visible.map((event) => <button key={event.id} onClick={() => void inspect(event)} disabled={!event.run_id} className="flex w-full flex-col justify-between gap-2 rounded-lg border p-4 text-left hover:bg-accent disabled:opacity-100 sm:flex-row sm:items-center">
        <span><strong>{formatEnum(event.event_type)}</strong><span className="mt-1 block text-xs text-muted-foreground">{event.strategy_name ?? event.account_number ?? "Workspace"}{event.symbol ? ` · ${event.symbol}` : ""} · <time title={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time></span>{event.reason_codes?.length ? <span className="mt-1 block text-xs">{event.reason_codes.map(formatEnum).join(", ")}</span> : null}</span>
        <span className="flex gap-2">{event.dry_run && <StatusPill label="Demo Test" />}<StatusPill label={formatEnum(event.outcome)} /></span>
      </button>)}
      {!visible.length && <p className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">No activity matches these filters.</p>}
    </div>
    <div className="flex items-center justify-between"><Button variant="outline" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Previous</Button><span className="text-sm">Page {page + 1} · {filtered.length} events</span><Button variant="outline" disabled={(page + 1) * PAGE_SIZE >= filtered.length} onClick={() => setPage((value) => value + 1)}>Next</Button></div>
    <Sheet open={Boolean(audit)} onOpenChange={(open) => !open && setAudit(null)}><SheetContent className="overflow-y-auto sm:max-w-xl"><SheetHeader><SheetTitle>Strategy evaluation</SheetTitle><SheetDescription>Structured audit fields only.</SheetDescription></SheetHeader>{audit && <div className="mt-5 space-y-3"><Datum label="Final action" value={formatEnum(audit.outcome)} /><Datum label="Markets checked" value={audit.selected_market_universe.join(", ") || "None"} /><Datum label="Candidates screened" value={String(audit.candidates_screened)} /><Datum label="Candidates analyzed" value={String(audit.candidates_deeply_analyzed)} /><Datum label="Selected instrument" value={audit.chosen_instrument ?? "None"} /><Datum label="Blocking reasons" value={audit.reason_codes.map(formatEnum).join(", ") || "None"} /></div>}</SheetContent></Sheet>
  </div>
}

function Choice({ label, value, onChange, options, empty }: { label: string; value: string; onChange: (value: string) => void; options: string[][]; empty: string }) {
  return <select value={value} onChange={(event) => onChange(event.target.value)} className="h-10 rounded border bg-background px-3" aria-label={label}><option value="">{empty}</option>{options.map(([raw, display]) => <option key={raw} value={raw}>{display}</option>)}</select>
}

function Datum({ label, value }: { label: string; value: string }) {
  return <div className="rounded border p-3"><p className="text-xs text-muted-foreground">{label}</p><p>{value}</p></div>
}
