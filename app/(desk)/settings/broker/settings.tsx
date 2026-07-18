"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { Connection, ProviderDescriptor } from "@/lib/afd/contracts"
import { formatEnum } from "@/lib/afd/display"
import { mutateAfd } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { StatusPill } from "@/components/desk/status-pill"

export function BrokerSettings({ connections, providers }: { connections: Connection[]; providers: ProviderDescriptor[] }) {
  const router = useRouter()
  const [busy, setBusy] = useState("")
  const [remove, setRemove] = useState<Connection | null>(null)
  const [status, setStatus] = useState("")
  const [choose, setChoose] = useState(false)
  async function act(path: string, method: "POST" | "PUT" | "DELETE") {
    setBusy(path); setStatus("Saving…")
    try { await mutateAfd(path, method); setStatus("Change confirmed."); setRemove(null); router.refresh() }
    catch { setStatus("Change failed. The previous connection state remains active.") }
    finally { setBusy("") }
  }
  const tradelocker = providers.find((provider) => provider.provider_type === "tradelocker")
  const tradingview = providers.find((provider) => provider.provider_type === "tradingview_chart")
  const tradingviewSignal = providers.find((provider) => provider.provider_type === "tradingview_signal")
  const robinhood = providers.find((provider) => provider.provider_type === "robinhood_mcp")
  return <div className="space-y-5">
    {status && <p role="status" className="rounded border p-3 text-sm">{status}</p>}
    <div className="flex justify-end"><Button onClick={() => setChoose(true)}>Add Trading Provider</Button></div>
    <div className="grid gap-4 lg:grid-cols-2">
      {connections.map((connection) => <Card key={connection.public_id}><CardHeader><div className="flex justify-between"><div><p className="text-xs font-medium uppercase text-muted-foreground">Broker</p><CardTitle>{connection.broker_name || connection.server || "HeroFX"}</CardTitle><p className="text-sm text-muted-foreground">Connected through {connection.platform_name || "TradeLocker"}</p></div><StatusPill label={connection.enabled ? "Connected" : "Needs Attention"} tone={connection.enabled ? "good" : "warn"} /></div></CardHeader><CardContent><p className="text-sm">Forex, Metals, Indices, Energy, Crypto, and CFDs</p><dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3"><Datum label="Environment" value={formatEnum(connection.environment)} /><Datum label="Accounts" value={String(connection.account_count)} /><Datum label="Last verified" value={connection.last_verified_at ? new Date(connection.last_verified_at).toLocaleString() : "Not recorded"} /></dl><div className="mt-5 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void act(`broker/tradelocker/discover-accounts?connection_id=${encodeURIComponent(connection.public_id)}`, "POST")}>Refresh Accounts</Button><Button asChild size="sm" variant="outline"><Link href={`/connect-tradelocker?connection_id=${encodeURIComponent(connection.public_id)}`}>Reauthenticate</Link></Button><Button size="sm" variant="ghost" className="text-red-600" onClick={() => setRemove(connection)}>Remove Connection</Button></div></CardContent></Card>)}
      <ProviderCard title="TradingView" role="Charts and Signals" detail="Chart context is non-authoritative and TradingView is not a broker." status={tradingview?.status === "available" ? `Charts Available · Signals ${tradingviewSignal?.status === "available" ? "Available" : "Not Configured"}` : "Not Configured"} />
      <ProviderCard title="Robinhood Financial" role="Stocks and Options" detail="Official Robinhood Agentic MCP boundary only. Execution is not configured." status={robinhood?.status === "not_configured" ? "Coming Later" : formatEnum(robinhood?.status)} />
    </div>
    <Card><CardHeader><CardTitle>Provider capabilities</CardTitle></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2">{providers.filter((provider) => provider.status !== "unsupported").map((provider) => <div key={provider.provider_type} className="rounded border p-3"><div className="flex justify-between"><strong>{provider.display_name}</strong><StatusPill label={formatEnum(provider.status)} /></div><p className="mt-1 text-xs text-muted-foreground">Roles: {provider.roles.map(formatEnum).join(", ") || "None"}</p><p className="text-xs text-muted-foreground">Assets: {provider.capabilities.asset_classes.map(formatEnum).join(", ") || "None reported"}</p><p className="text-xs">Order submission: {provider.capabilities.order_submission ? "Supported" : "Unavailable"}</p></div>)}</CardContent></Card>
    <Dialog open={choose} onOpenChange={setChoose}><DialogContent><DialogHeader><DialogTitle>Select a provider</DialogTitle></DialogHeader><div className="space-y-3"><ProviderChoice name="TradeLocker" role="Execution and account data" enabled={tradelocker?.status === "available"} href="/connect-tradelocker?new=1" /><ProviderChoice name="TradingView Signals" role="Authenticated signal ingress; never direct execution" enabled={false} /><ProviderChoice name="Robinhood Agentic" role="Official MCP connection" enabled={false} /></div></DialogContent></Dialog>
    <AlertDialog open={Boolean(remove)} onOpenChange={(open) => !open && setRemove(null)}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove {remove?.broker_name || remove?.server} connection?</AlertDialogTitle><AlertDialogDescription>This removes stored credentials when no strategies remain bound. It cannot be undone.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction disabled={Boolean(busy)} onClick={() => remove && void act(`trading/connections/${remove.public_id}`, "DELETE")}>Remove Connection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}

function ProviderCard({ title, role, detail, status }: { title: string; role: string; detail: string; status: string }) { return <Card><CardHeader><div className="flex justify-between"><div><CardTitle>{title}</CardTitle><p className="text-sm text-muted-foreground">{role}</p></div><StatusPill label={status} tone={status === "Available" ? "good" : "neutral"} /></div></CardHeader><CardContent><p className="text-sm">{detail}</p></CardContent></Card> }
function ProviderChoice({ name, role, enabled, href }: { name: string; role: string; enabled: boolean; href?: string }) { return <div className="flex items-center justify-between rounded border p-4"><span><strong>{name}</strong><small className="block text-muted-foreground">{role}</small></span>{enabled && href ? <Button asChild><Link href={href}>Continue</Link></Button> : <Button disabled>Not Configured</Button>}</div> }
function Datum({ label, value }: { label: string; value: string }) { return <div><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1">{value}</dd></div> }
