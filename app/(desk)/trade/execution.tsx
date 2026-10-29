"use client"

import { useEffect, useRef, useState } from "react"
import { BrowserAfdError, browserAfdFetch } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export type ApprovedOrder = {
  preview_id: string; expires_at: string; profile_ref: string; account_id: string;
  pair: string; side: string; entry: number; stop_loss: number; take_profit: number;
  lot_size: number; estimated_risk: number; estimated_margin: number; reward_risk: number;
}
type Outcome = { status: string; execution_id?: string; broker_order_id?: string; broker_position_id?: string; manual_review_required?: boolean; message?: string }
export type ManualProfile = { public_id: string; account_id: string; name: string; enabled: boolean; execution_mode: string; account_environment: string }

function message(cause: unknown) { return cause instanceof Error ? cause.message : "The request could not be completed." }
function label(status: string) {
  return status === "submitted" || status === "verified" ? "Broker verified" : status === "rejected" ? "Order rejected" : "Broker confirmation needed"
}

export function SubmissionControls({ account, preview, busy, canReview, review, edit, onComplete }: {
  account: string; preview: ApprovedOrder | null; busy: boolean; canReview: boolean;
  review: () => void; edit: () => void; onComplete: () => void;
}) {
  const [sending, setSending] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [error, setError] = useState("")
  const [pending, setPending] = useState<{ order: ApprovedOrder; key: string } | null>(null)
  const lock = useRef(false)
  const [now, setNow] = useState(Date.now())
  const storageKey = `afd-ticket-pending:${account}`
  useEffect(() => {
    setOutcome(null); setError(""); setPending(null)
    const raw = sessionStorage.getItem(storageKey)
    if (raw) { try { const saved = JSON.parse(raw); if (saved.order?.account_id === account && typeof saved.key === "string") setPending(saved) } catch { sessionStorage.removeItem(storageKey) } }
  }, [account, storageKey])
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  const expired = Boolean(preview && Date.parse(preview.expires_at) <= now)
  async function submit() {
    const order = pending?.order ?? preview
    if (!order || lock.current) return
    const request = pending ?? { order, key: crypto.randomUUID() }
    lock.current = true; setSending(true); setError("")
    setPending(request); sessionStorage.setItem(storageKey, JSON.stringify(request))
    try {
      const result = await browserAfdFetch<Outcome>("trading/order-submissions", { method: "POST", body: JSON.stringify({ preview_id: request.order.preview_id, idempotency_key: request.key, confirmed: true }) })
      setOutcome(result)
      if (["submitted", "verified", "rejected"].includes(result.status)) { setPending(null); sessionStorage.removeItem(storageKey); onComplete() }
    } catch (cause) {
      if (cause instanceof BrowserAfdError && [404,409,422].includes(cause.status)) { setPending(null); sessionStorage.removeItem(storageKey); setOutcome({status:"rejected"}); setError(message(cause)) }
      else setError(`${message(cause)} Check the submission using the same request before preparing another order.`)
    }
    finally { lock.current = false; setSending(false) }
  }
  async function reconcile() {
    if (!outcome?.execution_id) return submit()
    setSending(true); setError("")
    try {
      const result = await browserAfdFetch<Outcome>(`trading/executions/${encodeURIComponent(outcome.execution_id)}`)
      setOutcome(result)
      if (["submitted", "verified", "rejected"].includes(result.status)) { setPending(null); sessionStorage.removeItem(storageKey); onComplete() }
    } catch (cause) { setError(message(cause)) } finally { setSending(false) }
  }
  if (pending || outcome) return <div className="space-y-3 border-t border-border/40 pt-5" aria-live="polite">
    <p className="text-sm font-medium">{outcome ? label(outcome.status) : "Submission awaiting confirmation"}</p>
    {outcome?.broker_order_id && <p className="text-xs text-muted-foreground">Broker order {outcome.broker_order_id}</p>}
    {error && <p role="alert" className="text-xs text-amber-600">{error}</p>}
    {pending ? <><p className="text-xs leading-relaxed text-muted-foreground">The broker may have received this order. Check its status; do not create a replacement order.</p><Button disabled={sending} onClick={() => void reconcile()} className="w-full rounded-full">{sending ? "Checking…" : "Check submission"}</Button></> : <Button onClick={() => { setOutcome(null); edit() }} variant="outline" className="w-full rounded-full">New order</Button>}
  </div>
  return <div className="space-y-3 border-t border-border/40 pt-5">
    {preview ? <>
      <p className="text-sm font-medium">Review this demo order</p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
        {Object.entries({ Order: `${preview.side === "long" ? "Buy" : "Sell"} ${preview.lot_size} lots · ${preview.pair}`, Entry: preview.entry, "Stop loss": preview.stop_loss, "Take profit": preview.take_profit, "Estimated risk": preview.estimated_risk.toFixed(2), "Estimated margin": preview.estimated_margin.toFixed(2) }).map(([name, value]) => <div key={name}><dt className="text-muted-foreground">{name}</dt><dd className="mt-1 font-medium">{value}</dd></div>)}
      </dl>
      <p className="text-xs text-muted-foreground">{expired ? "Preview expired. Edit and review again." : `Valid for ${Math.max(0, Math.ceil((Date.parse(preview.expires_at) - now) / 1000))} seconds. Final fill may differ.`}</p>
      <Button disabled={sending || expired} onClick={() => void submit()} className="w-full rounded-full">{sending ? "Submitting…" : "Confirm & submit demo order"}</Button>
      <Button disabled={sending} onClick={edit} variant="ghost" className="w-full rounded-full">Edit order</Button>
    </> : <><Button disabled={busy || !canReview} onClick={review} className="w-full rounded-full">{busy ? "Validating…" : "Review demo order"}</Button><p className="text-center text-xs leading-relaxed text-muted-foreground">Review creates a preview. Only your confirmation sends an order.</p></>}
  </div>
}

type BrokerRow = { id: string; symbol: string; side: string | null; quantity: number | null; entry: number | null; pnl: number | null; stop_loss: number | null; take_profit: number | null; status: string | null; type: string | null }
type Activity = { rows: BrokerRow[]; checked_at: string; account_id: string }
type Action = { preview_id: string; expires_at: string; action: string; target: Record<string, unknown> }

export function BrokerActivity({ account, view, profile, demo, revision }: { account: string; view: "positions" | "pending" | "history"; profile: string; demo: boolean; revision: number }) {
  const [activity, setActivity] = useState<Activity | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [action, setAction] = useState<Action | null>(null)
  const [result, setResult] = useState<Outcome | null>(null)
  const [actionKey, setActionKey] = useState("")
  const lock = useRef(false)
  const actionStorage = `afd-action-pending:${account}:${view}`
  useEffect(() => {
    setAction(null); setResult(null); setActivity(null)
    try { const raw=sessionStorage.getItem(actionStorage); if(raw){const saved=JSON.parse(raw); if(saved.action?.preview_id && typeof saved.key === "string"){setAction(saved.action);setActionKey(saved.key);setResult(saved.result??null)}} } catch {sessionStorage.removeItem(actionStorage)}
  }, [account, view, actionStorage])
  useEffect(() => {
    if (!account) return
    const controller = new AbortController(); let stopped = false; let timer: ReturnType<typeof setTimeout>
    async function load() {
      if (!document.hidden) {
        try { const data = await browserAfdFetch<Activity>(`trading/account-activity?account_id=${encodeURIComponent(account)}&view=${view}`, { signal: controller.signal }); if (!stopped) { setActivity(data); setError("") } }
        catch (cause) { if (!stopped) setError(message(cause)) }
      }
      if (!stopped) timer = setTimeout(load, 15000)
    }
    void load(); return () => { stopped = true; controller.abort(); clearTimeout(timer) }
  }, [account, view, refresh, revision])
  async function prepare(row: BrokerRow) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(""); setResult(null)
    try { setAction(await browserAfdFetch<Action>("trading/action-previews", { method: "POST", body: JSON.stringify({ profile_ref: profile, action: view === "positions" ? "close_position" : "cancel_order", target_id: row.id }) })); setActionKey(crypto.randomUUID()) }
    catch (cause) { setError(message(cause)) } finally { lock.current = false; setBusy(false) }
  }
  async function confirm() {
    if (!action || lock.current) return
    lock.current = true; setBusy(true); setError("")
    try {
      sessionStorage.setItem(actionStorage, JSON.stringify({action,key:actionKey,result}))
      const data = result?.execution_id
        ? await browserAfdFetch<Outcome>(`trading/executions/${encodeURIComponent(result.execution_id)}`)
        : await browserAfdFetch<Outcome>("trading/action-submissions", { method: "POST", body: JSON.stringify({ preview_id: action.preview_id, idempotency_key: actionKey, confirmed: true }) })
      setResult(data)
      if (["verified", "rejected"].includes(data.status)) { setAction(null); sessionStorage.removeItem(actionStorage); setRefresh(n => n + 1) }
      else sessionStorage.setItem(actionStorage, JSON.stringify({action,key:actionKey,result:data}))
    } catch (cause) {
      if (cause instanceof BrowserAfdError && [404,409,422].includes(cause.status) && !result?.execution_id) {setAction(null);sessionStorage.removeItem(actionStorage);setResult({status:"rejected"})}
      else {const unknown={status:"unknown",manual_review_required:true};setResult(unknown);sessionStorage.setItem(actionStorage,JSON.stringify({action,key:actionKey,result:unknown}))}
      setError(`${message(cause)} Refresh broker activity before taking another action.`)
    }
    finally { lock.current = false; setBusy(false) }
  }
  return <Card className="mt-5"><CardHeader className="flex flex-row items-center justify-between gap-3"><CardTitle>{view === "positions" ? "Open positions" : view === "pending" ? "Pending orders" : "Order history"}</CardTitle><Button variant="ghost" onClick={() => setRefresh(n => n + 1)}>Refresh</Button></CardHeader><CardContent className="space-y-5">
    {error && <p role="alert" className="text-sm text-amber-600">{error}{activity ? " Previously loaded rows may be outdated." : ""}</p>}
    {result && <p role="status" className="text-sm">{label(result.status)}{result.manual_review_required ? ". Check the broker before repeating this action." : ""}</p>}
    {action && <div className="space-y-3 rounded-2xl bg-muted/50 p-5"><p className="text-sm font-medium">{action.action === "close_position" ? "Close this entire demo position at market?" : "Cancel this demo order?"}</p><p className="text-xs text-muted-foreground">Broker target {String(action.target.positionId ?? action.target.orderId ?? action.target.id ?? "")} · This changes the demo account.</p><div className="flex flex-wrap gap-3"><Button disabled={busy} onClick={() => void confirm()}>{busy ? "Checking…" : result?.manual_review_required ? "Check action status" : "Confirm action"}</Button><Button disabled={busy || Boolean(result?.manual_review_required)} variant="ghost" onClick={() => setAction(null)}>Dismiss</Button></div></div>}
    {!account ? <p className="text-sm text-muted-foreground">Select an account to view broker activity.</p> : !activity ? <p className="text-sm text-muted-foreground">{error ? "Broker activity unavailable." : "Loading broker activity…"}</p> : activity.rows.length === 0 ? <p className="py-8 text-sm text-muted-foreground">No {view === "positions" ? "open positions" : view === "pending" ? "pending orders" : "order history"} reported by this broker account.</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-muted-foreground"><tr>{["Market", "Side / type", "Lots", "Entry", "Stop / target", "P&L / status", ""].map((name, i) => <th key={i} className="px-3 pb-4 font-normal">{name}</th>)}</tr></thead><tbody>{activity.rows.map((row, i) => <tr key={`${row.id}-${i}`} className="border-t border-border/40"><td className="px-3 py-5 font-medium">{row.symbol}<span className="mt-1 block text-[10px] text-muted-foreground">#{row.id}</span></td><td className="px-3 py-5">{row.side ?? "—"}<span className="block text-xs text-muted-foreground">{row.type}</span></td><td className="px-3 py-5">{row.quantity ?? "—"}</td><td className="px-3 py-5">{row.entry ?? "—"}</td><td className="px-3 py-5">{row.stop_loss ?? "—"} / {row.take_profit ?? "—"}</td><td className="px-3 py-5">{row.pnl ?? row.status ?? "—"}</td><td className="px-3 py-5">{view !== "history" && demo && profile && <Button size="sm" variant="outline" disabled={busy || Boolean(action)} onClick={() => void prepare(row)}>{view === "positions" ? "Close" : "Cancel"}</Button>}</td></tr>)}</tbody></table></div>}
    {activity && <p className="text-xs text-muted-foreground">TradeLocker · Updated {new Date(activity.checked_at).toLocaleTimeString()} · Refreshes every 15 seconds while visible.</p>}
  </CardContent></Card>
}
