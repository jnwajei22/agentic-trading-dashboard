"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import type { Account } from "@/lib/afd/contracts"
import { accountContext, accountTitle, formatEnum } from "@/lib/afd/display"
import { mutateAfd } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { StatusPill } from "@/components/desk/status-pill"
import { EmptyState } from "@/components/desk/page-header"

export function AccountSettings({ accounts }: { accounts: Account[] }) {
  const router = useRouter(); const [names, setNames] = useState(Object.fromEntries(accounts.map((account) => [account.public_id, account.nickname ?? ""])))
  const [busy, setBusy] = useState(""); const [status, setStatus] = useState("")
  async function act(path: string, body?: object) { setBusy(path); setStatus("Saving…"); try { await mutateAfd(path, "PUT", body); setStatus("Change confirmed."); router.refresh() } catch { setStatus("Change failed. Previous state remains active.") } finally { setBusy("") } }
  return <div className="space-y-3">{status && <p role="status" className="rounded border p-3 text-sm">{status}</p>}{accounts.map((account) => <Card key={account.public_id}><CardContent className="grid gap-4 p-4 lg:grid-cols-[1.2fr_1fr_1fr_auto]"><div><div className="flex flex-wrap items-center gap-2"><strong className="text-lg">Account {accountTitle(account)}</strong>{account.is_default_analysis && <StatusPill label="Primary Account" tone="good" />}</div><p className="text-sm text-muted-foreground">{accountContext(account)} · {account.currency ?? "Currency unavailable"}</p><p className="text-xs text-muted-foreground">Platform: {account.platform_name ?? "Unavailable"} · Provider: {formatEnum(account.provider_type)} · Verified {account.last_verified_at ? new Date(account.last_verified_at).toLocaleString() : "not recorded"}</p>{account.asset_classes?.length ? <p className="text-xs text-muted-foreground">Assets: {account.asset_classes.map(formatEnum).join(", ")}</p> : null}</div><div><p className="text-xs text-muted-foreground">Optional nickname</p><div className="mt-1 flex gap-2"><Input value={names[account.public_id]} onChange={(event) => setNames((value) => ({ ...value, [account.public_id]: event.target.value }))} placeholder="Nickname" /><Button size="sm" variant="outline" onClick={() => act(`trading/accounts/${account.public_id}/nickname`, { nickname: names[account.public_id] })}>Save</Button></div></div><div className="grid grid-cols-2 gap-3 text-sm"><Datum label="Bound strategies" value={String(account.profiles.length)} /><Datum label="Availability" value={account.available ? "Available" : "Unavailable"} /><Datum label="Positions" value={account.capabilities?.positions ? "Supported" : "Unavailable"} /><Datum label="Order submission" value={account.capabilities?.order_submission ? "Supported" : "Unavailable"} /></div><div className="flex flex-wrap items-center gap-2">{!account.is_default_analysis && <Button size="sm" variant="outline" disabled={Boolean(busy) || !account.available} onClick={() => act(`trading/accounts/${account.public_id}/primary`)}>Set as Primary</Button>}<Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => act(account.locally_enabled ? `broker/accounts/${account.public_id}/disable` : `trading/accounts/${account.public_id}/enable`)}>{account.locally_enabled ? "Disable" : "Enable"}</Button></div></CardContent></Card>)}{!accounts.length && <EmptyState title="No trading accounts" description="Add a trading connection to discover accounts." />}</div>
}

function Datum({ label, value }: { label: string; value: string }) { return <div><p className="text-xs text-muted-foreground">{label}</p><p>{value}</p></div> }
